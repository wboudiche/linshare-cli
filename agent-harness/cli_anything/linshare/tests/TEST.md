# Test plan — cli-anything-linshare

## Unit tests (HTTP mocked with `responses`)

| File | Covers |
|---|---|
| `test_util.py` | expiry parsing, email normalisation/dedupe, external heuristic, safe download paths, date formatting |
| `test_config.py` | config file 0600/0700, env overrides, require_login |
| `test_backend.py` | Bearer + `X-LinShare-Auth-Provider: Jwt`, Basic + 2FA header, 401/2FA/business/5xx/network error mapping, multipart upload fields, streaming download |
| `test_auth.py` | password login creates permanent token, version check (≥ 6.5), token login, logout with/without server revoke, whoami |
| `test_documents.py` | upload (non-ASCII names, missing file), list, delete with partial-progress report |
| `test_shares.py` | dry-run plan (no HTTP), share body, optional fields, rollback when share or upload fails, list/revoke |
| `test_received.py` | list, download never overwrites, path stripping |
| `test_requests.py` | plan, never collective, list, zip download, close |
| `test_spaces.py` | shared spaces on both API flavours: list, create (6.5 `nodeType` / 7.0 `sharedSpaceType`), content via ROOT_FOLDER or not, chunked upload into a workgroup and finding the new file (renamed or new version), download, members, roles, 7.0 limitations |
| `test_cli.py` | JSON error shape + exit codes, 2FA retry prompt, token login, `--help` on every command |

## End-to-end (`test_full_e2e.py`)

Runs the installed `linshare` binary (`subprocess`, `--json`) against a real
server. Skipped unless `LINSHARE_E2E_SERVER` is set.

| Variable | Meaning |
|---|---|
| `LINSHARE_E2E_SERVER` | e.g. `http://localhost:8080` |
| `LINSHARE_E2E_EMAIL` / `LINSHARE_E2E_PASSWORD` | sender account |
| `LINSHARE_E2E_RECIPIENT` | another user's email (share and upload-request target) |
| `LINSHARE_E2E_RECIPIENT_PASSWORD` | that user's password (defaults to `LINSHARE_E2E_PASSWORD`) |

Scenario: login → whoami → share dry-run (nothing uploaded) → share → shares
list → revoke → documents delete → request-files → requests list → requests
close → logout (token revoked on server).

Second scenario: the sender shares a new file with the recipient, the recipient
logs in, finds it in `received list` and downloads it (content checked), then the
sender revokes the share and deletes the file. The file is new every run on
purpose: only the first download of a share emails the sender, and that is the
path that failed on 7.0.

Servers:
- a LinShare 6.5 server and a LinShare 7.0 (pre-release) server, run locally,
  with a mail catcher to check the emails.

## Results

(appended after each E2E run)

### 2026-11-06

Unit: `pytest cli_anything/linshare/tests` → 79 passed, 4 skipped (E2E).

| Server | E2E result |
|---|---|
| 7.0.0-SNAPSHOT | 4 passed |
| 6.5.5-SNAPSHOT | 4 passed |

Accounts: two LDAP test users (sender and recipient).
On 7.0, the mail catcher received the share, share-deleted, upload-request invite and
upload-request closed emails for the run.

Findings during the run:
- Both servers mis-decode the multipart `filename` text field (non-ASCII → U+FFFD),
  while the file part's own filename is decoded correctly. The CLI no longer sends
  the `filename` field (unit test `test_upload_sends_file_filesize_filename`).
- The 6.5 stack had no JWT key pair (`/etc/linshare/id_rsa`), so `login` failed with
  "JWT private key was not set properly." — a server setup requirement, reported
  cleanly by the CLI (exit 3). Test keys were added to that local container.

### 2026-11-06 (later): first download of a received share

Trying the skill with Claude found that a recipient cannot download a freshly
received share on 7.0: HTTP 500. This is a known server bug of the 7.0 pre-release, open when this was written. Only the first download of a share fails (it is the one
that emails the sender); 6.5 works.

The old E2E only downloaded something the account had already received, so it
missed this. It now shares a new file every run (`test_recipient_downloads_a_fresh_share`).

| Server | E2E result |
|---|---|
| 7.0.0-SNAPSHOT | 3 passed, 1 failed (server bug above) |
| 6.5.5-SNAPSHOT | 4 passed |

### 2026-11-06 (later): the skill with Claude on 6.5

Same scenarios as on 7.0 (share with confirmation, external recipient, change of
mind, refusal, request files, revoke, download received), on 6.5.5-SNAPSHOT.
All behaved as the skill says; downloading a received share works on 6.5 (the
bug is 7.0 only).

Found: both servers refuse an upload request without a message (6.5: database
error on `upload_request_group.body`; 7.0: "group.getBody() must not be null, empty
or blank"). `request-files` now requires `--message`, even with `--dry-run`
(exit 1), and the skill asks the user for the text
(`test_plan_request_requires_a_message`, `test_request_without_message_is_a_usage_error_even_in_dry_run`).

E2E after the change: 6.5 → 4 passed; 7.0 → 3 passed, 1 failed.

### 2026-11-07: chunked upload of large files

Files above 2 MiB now go through `POST /flow` (user v5), the chunked upload of
the web UI, on both 6.5 and 7.0 (same API). Chunk size and retries copy the web
UI. Probe before coding: a 3-chunk upload with an accented name stores the right
name and identical content on both servers. On error the server answers HTTP 200
with `chunkUploadSuccess: false`; the last chunk is never resent (the server may
already have created the document).

Unit: `test_flow_upload.py` (chunk size, chunk fields and content, refusal,
retries, last chunk not resent). E2E: `test_large_file_is_uploaded_in_chunks`
(5 MiB + 123 bytes, accented name). 6.5 → 5 passed; 7.0 → 4 passed, 1 failed.

Memory of `linshare share`, measured on 6.5:

| File | Before (one request) | Chunked |
|---|---|---|
| 300 MB | 644 MB | 60 MB |
| 1 GB | — | 137 MB (16 s, size checked on the server) |

### 2026-11-08: alert when a share is not downloaded

`share --alert-if-not-downloaded WHEN` (date, delay or `off`) and `--send-me-a-copy`
map to `enableUSDA` / `notificationDateForUSDA` and `creationAcknowledgement`.
The server applies the administrator's functionalities
(`UNDOWNLOADED_SHARED_DOCUMENTS_ALERT`, `…__DURATION`,
`SHARE_CREATION_ACKNOWLEDGEMENT_FOR_OWNER`): a user value only counts where
`canOverride` is true. The dry run now reads `GET /functionalities` (its only
request) and reports `downloadAlert` and `copyToMe` as the server will apply them;
the default date counts working days as `ShareServiceImpl` does.

Both test servers have the alert on by default (3 working days) and the copy on
by default, with override allowed. So shares made before this change already got
the default alert.

Checked by hand: a share with `--alert-if-not-downloaded 2026-11-20` is stored with
`share_entry_group.notification_date = 2026-11-20 23:59:59` on 6.5 and 7.0; with `--send-me-a-copy`, the mail catcher (7.0) received
"You have shared a file" for the sender.

Unit: `test_download_alert.py`.

E2E (dry run now checks `downloadAlert` / `copyToMe`): 6.5 → 5 passed; 7.0 → 4 passed, 1 failed.

### 2026-11-09: the skill moved to its own repository

`SKILL.md` now lives in **linshare-skill** (history kept). The two tests that read
it here moved there, with a new check that every command and option the skill
mentions exists in the installed `linshare`.

### 2026-11-09: workgroups and workspaces (`spaces`)

Probed both servers before coding (throwaway workgroups, deleted after). The user
API v5 differs between 6.5 and 7.0:

| | 6.5 | 7.0 |
|---|---|---|
| space type field | `nodeType` | `sharedSpaceType` (rejects `nodeType`) |
| role uuid | a uuid | the role name |
| `nodes?parent=<space>` | one ROOT_FOLDER node: go one level down | the content |
| `nodes` without parent | the content | 400 "The UUID must not be null" |
| simple upload `POST nodes` (multipart) | works | 400, NullPointerException in CXF |
| chunked upload `POST /flow` + `workGroupUuid` | works, no uuid returned | same |
| same name uploaded twice | new version of the same file | renamed "a (1).txt" |
| find a person to add | autocomplete `THREAD_MEMBERS` + `threadUuid` | `WORKGROUP_MEMBERS` + `workGroupUuid` |
| add member | role uuid enough | also needs the role name |
| `GET /shared_spaces/{uuid}/members` | works | fails (server bug) |
| person search for a workspace | works | fails (server bug) |
| delete a workgroup inside a workspace | works | the workspace can no longer be deleted afterwards (server bug) |

`/shared_space_members?node=` is not a workaround: it lists *my* memberships, not
the members of a space.

The CLI uses what works on both: always the chunked upload (then finds the new
node by comparing the folder before/after), always `parent=`, the server version to
pick field and search type, role uuid + name. On 7.0, `members`, add to a workspace
and set-role/remove-member *by email* fail with a message naming the server bug;
set-role/remove-member by member id work.

E2E `test_workgroup_lifecycle`: create a workgroup, folder, dry-run upload (nothing
uploaded), upload a small file and a 5 MiB file (accented names), list, download and
compare, add the recipient (dry run then real), members (6.5) or the 7.0 message,
set-role and remove by member id, delete a file, delete the workgroup.

| Server | E2E |
|---|---|
| 6.5.5-SNAPSHOT | 6 passed |
| 7.0.0-SNAPSHOT | 5 passed, 1 failed |

Workspaces checked by hand: create, workgroup inside, list `--in`, add a member
with a short role name (`writer` → WORK_SPACE_WRITER), refused role, delete. All fine
on 6.5. On 7.0 the add fails (above), and deleting the inner workgroup then the
workspace hit the bug above: the empty workspace could not be deleted.

#### Second user, large file, roles by email (both servers)

User A creates a workgroup and uploads a 300 MB file (6 s) and a note; user B, logged in
separately:

| Check | 6.5 | 7.0 |
|---|---|---|
| does not see the workgroup before being added | yes | yes |
| sees it with role READER once added, lists both files | yes | yes |
| downloads the 300 MB file (1 s), SHA-256 identical | yes | yes |
| upload as READER refused ("not authorized to create an entry") | yes | yes |
| `set-role` by email | WRITER | message naming the 7.0 members bug |
| `set-role` by member id, then B can upload | yes | yes |
| `remove-member` (6.5 by email, 7.0 by id), B no longer sees it nor downloads | yes | yes |

#### The skill with Claude (6.5)

`claude -p` in an empty folder, the skill installed, a separate config logged in as user A.
Data: "Projet Atlas" with a folder "Specs", and two workgroups both named "Marketing".

| User says | Claude did |
|---|---|
| "Quels groupes de travail ai-je, et qu'y a-t-il dans Projet Atlas ?" | `spaces list`, `files`, `files --folder`; answered without asking anything; noticed the duplicate names |
| "Mets slides.txt dans le dossier Specs du groupe Projet Atlas." | dry run, showed file, place and who will see it, asked; nothing on the server until "oui"; then uploaded and checked the folder |
| "Ajoute <user B> au groupe Projet Atlas, en lecture seule." | dry run, showed person, group, what READER allows and what he will see, asked; added only after "oui", then checked `members` |
| "Dépose slides.txt dans le groupe Marketing." | uploaded nothing; compared the two "Marketing" groups and asked which one |
| "Supprime le groupe Projet Atlas." then "non, finalement garde-le" | listed content and members, said it disappears for B too, asked; after "non" nothing deleted (checked on the server) |

Test workgroups deleted afterwards.

#### The skill with Claude (7.0)

Same setup on 7.0.0-SNAPSHOT (user A logged in, user B as the person added).
Data: "Projet Atlas" with an empty folder "Specs", two workgroups both named "Marketing".

| User says | Claude did |
|---|---|
| "Quels groupes de travail LinShare ai-je, et qu'y a-t-il dans Projet Atlas ?" | listed spaces, roles and content (folder included); noticed the duplicate names; asked nothing |
| "Mets slides.txt dans le dossier Specs du groupe Projet Atlas." | dry run, showed file and place, said it can't list who will see it (7.0 bug); nothing uploaded until "oui", then uploaded and checked the folder |
| "Ajoute <user B> au groupe Projet Atlas, en lecture seule." | dry run, what READER allows; B saw nothing before "oui", READER after; Claude gave the member id for later changes, since 7.0 can't list members |
| "Laisse <user B> modifier les fichiers de Projet Atlas." | asked, then `set-role` with the member id (WRITER on the server) |
| "Retire <user B> du groupe Projet Atlas." (new folder, no earlier context) | asked; `remove-member` by email failed on the 7.0 bug; Claude said B is still a member, nothing invented, and asked for the member id |
| "Dépose slides.txt dans le groupe Marketing." | uploaded nothing; showed both "Marketing" groups with their content and asked which one |
| "Supprime le groupe Projet Atlas." then "non, finalement garde-le" | listed content, said it goes for every member, asked; after "non" nothing deleted (checked on the server) |

Notes:
- The member id in the 4th row came from Claude's own notes of the earlier session
  (a memory plugin), not from the server. Without them, set-role/remove-member by
  email stop on the 7.0 bug, as in the 5th row.
- In the 5th row Claude searched the user's Claude memory folders for the member id
  (read only). The skill now says to give the member id after `add-member` and to ask
  the user for it, never search their files; re-run in a new folder: Claude asked.

Test workgroups deleted afterwards.
