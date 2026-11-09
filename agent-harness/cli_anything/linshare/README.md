# cli-anything-linshare

Share documents through a LinShare server (6.5 or later) from the command line,
designed to be driven by AI agents (Claude Code, Codex, Cursor...) — a
[CLI-Anything](https://github.com/HKUDS/CLI-Anything) harness.

## Install

Python 3.10 or later.

```bash
pip install "git+https://github.com/wboudiche/linshare-cli#subdirectory=agent-harness"
# or, from a clone:
cd agent-harness && pip install -e .
```

## Log in (once)

```bash
linshare login --server https://linshare.example.com
```

You type your email and password (and your 2FA code if your account uses one).
The tool creates a long-lived LinShare token and stores it in
`~/.cli-anything-linshare/config.json` (readable only by you). Your password is
never stored. If you sign in to LinShare with SSO, create a permanent token in
the LinShare web UI and run `linshare login --token`.

Your LinShare domain must allow permanent (JWT) tokens.

## Use

```bash
linshare share report.pdf --to marc@acme.com --expires 7d --dry-run
linshare share report.pdf --to marc@acme.com --expires 7d
linshare shares list
linshare received list
linshare received download <id> --out ~/Downloads
linshare request-files --from ann@acme.com --message "Please send the scans"
linshare --json requests list
```

Files above 2 MiB are uploaded in chunks, as the LinShare web UI does
(chunks of 2 % of the file, between 2 and 100 MiB; each chunk retried up to 3
times). Memory stays low whatever the size (about 140 MB for a 1 GB file).
The server's own limits still apply (maximum file size, quota).

Downloads: LinShare emails you the first time each recipient downloads a share,
and `linshare shares list` shows how many times each share was downloaded.
`share --alert-if-not-downloaded 2026-11-20` (or `3d`, or `off`) asks for an email
on that day if the share is still not downloaded; `--send-me-a-copy` emails you
the share notification too. The administrator may decide these for you: the dry
run reads the server's settings and shows what will really happen
(`downloadAlert`, `copyToMe`).

Run `linshare` without arguments for an interactive shell. Add `--json`
(before the command) for machine-readable output.

## Commands

| Command | What it does |
|---|---|
| `login [--server URL] [--email E]` / `login --token` | log in once (password + 2FA, or a token from the web UI) |
| `logout` | forget the token and revoke it on the server |
| `whoami` | server, version and account in use |
| `share FILE... --to EMAIL... [--expires 7d\|2w\|1m\|YYYY-MM-DD] [--subject S] [--message M] [--password-link] [--alert-if-not-downloaded WHEN] [--send-me-a-copy] [--dry-run]` | upload and share by email |
| `shares list` / `shares revoke ID...` | what you shared (with download counts) / cancel shares |
| `received list` / `received download ID [--out DIR]` | what others shared with you / save a file (never overwrites) |
| `documents list` / `documents delete ID...` | your documents |
| `request-files --from EMAIL... --message M [--label L] [--expires ...] [--dry-run]` | ask people to send you files (one private request each) |
| `requests list` / `requests download ID [--out DIR]` / `requests close ID...` | your upload requests, the files received (zip), close them |
| `spaces list [--in WORKSPACE]` | workgroups and workspaces you belong to, with your role |
| `spaces create NAME [--workspace \| --in WORKSPACE]` / `spaces delete ID...` | create a workgroup (or a workspace, or a workgroup inside one) / delete with all its files |
| `spaces files SPACE [--folder ID]` / `spaces mkdir SPACE NAME [--folder ID]` | browse a workgroup / create a folder |
| `spaces upload SPACE FILE... [--folder ID] [--dry-run]` | put files in a workgroup (any size, chunked); the dry run lists who will see them |
| `spaces download SPACE ID [--out DIR]` / `spaces delete-files SPACE ID...` | save a file (never overwrites) / delete files or folders |
| `spaces members SPACE` | members and their roles |
| `spaces add-member SPACE EMAIL --role R [--dry-run]` | give access: READER, CONTRIBUTOR, WRITER, ADMIN (workspaces: READER, WRITER, ADMIN) |
| `spaces set-role SPACE MEMBER ROLE` / `spaces remove-member SPACE MEMBER` | MEMBER is an email or the member id printed by add-member |

`--to` and `--from` are repeated for several people. A recipient is marked
"external" when their email domain differs from yours (a heuristic).
`linshare <command> --help` gives the details.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | done |
| 1 | wrong command or input (nothing was sent) |
| 2 | not logged in, or the login failed |
| 3 | the server refused (quota, policy, unknown item…) |
| 4 | server unreachable, failing or too slow (`"code": "timeout"`: the outcome is unknown, check the lists before retrying) |

With `--json`, errors are printed as `{"error": {"code", "message", "httpStatus", "linshareErrorCode"}}`.

## Compatibility

Uses the LinShare user API v5. Tested against LinShare 6.5 and 7.0 (see
`tests/TEST.md`). Known server bug on 7.0: downloading a share you have just
received fails with HTTP 500 (`received download`).

Shared spaces on 7.0 (server bugs, found while writing `spaces`):
- `spaces members` fails: the server cannot list the members of a shared space.
  `set-role` and `remove-member` still work with the member id instead of the email.
- `spaces add-member` cannot add someone to a *workspace* (its user search only
  accepts workgroups); adding to a workgroup works.
- Deleting a workgroup that is inside a workspace leaves a trace in the database,
  and the workspace can then no longer be deleted.

Everything else works on both.

## Agent skill

The skill that teaches an AI assistant to use this tool (and to always ask for
confirmation before sharing) lives in its own repository, [linshare-skill](https://github.com/wboudiche/linshare-skill).
Its tests check that every command and option it mentions exists here, so run
them after changing a command or an option.

## Environment

- `LINSHARE_SERVER`, `LINSHARE_TOKEN` override the stored login.
- `LINSHARE_CONFIG_DIR` changes the config directory.

## Tests

```bash
pip install -e '.[dev]'
pytest cli_anything/linshare/tests
```

End-to-end tests against a real server: see `tests/TEST.md`.

## License

AGPL-3.0
