"""Shared spaces: workgroups (files shared by a team) and workspaces (groups of workgroups).

The user API v5 differs between LinShare 6.5 and 7.0, and some 7.0 endpoints are broken.
What works on both, as checked against real servers (see TEST.md):
- type field: `nodeType` on 6.5, `sharedSpaceType` on 7.0 (read both; write the server's one);
- role uuid: a real uuid on 6.5, the role name on 7.0 (always send uuid and name);
- content: `nodes?parent=<space>`; 6.5 answers with a ROOT_FOLDER node, so go one level down;
- upload: only the chunked "flow" upload works on 7.0, and it does not return the new node;
- finding a person to add: search type THREAD_MEMBERS on 6.5, WORKGROUP_MEMBERS on 7.0;
- members list: broken on 7.0 (server bug), so commands also accept a member id.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from cli_anything.linshare.core.documents import check_readable_file, delete_each
from cli_anything.linshare.core.errors import LinShareError, ServerError, UsageError
from cli_anything.linshare.core.util import iso_from_ms, normalize_emails, safe_target_path
from cli_anything.linshare.utils.linshare_backend import LinShareClient

WORK_GROUP = "WORK_GROUP"
WORK_SPACE = "WORK_SPACE"
WS_PREFIX = "WORK_SPACE_"
MEMBERS_BUG_7 = (
    "LinShare 7.0 cannot list the members of a shared space (server bug in the user API v5:"
    " GET /shared_spaces/{uuid}/members fails)"
)


# -- server flavour -------------------------------------------------------------------


def is_v7(client: LinShareClient) -> bool:
    cached = getattr(client, "_linshare_v7", None)
    if cached is None:
        version = str((client.get("/authentication/version") or {}).get("version", ""))
        cached = version.startswith("7")
        client._linshare_v7 = cached
    return cached


def _type(raw: dict) -> str | None:
    return raw.get("sharedSpaceType") or raw.get("nodeType")


# -- spaces ---------------------------------------------------------------------------


def _project_space(s: dict) -> dict:
    return {
        "uuid": s.get("uuid"),
        "name": s.get("name"),
        "type": _type(s),
        "role": (s.get("role") or {}).get("name"),
        "parentUuid": s.get("parentUuid"),
        "modificationDate": iso_from_ms(s.get("modificationDate")),
    }


def list_spaces(client: LinShareClient, parent: str | None = None) -> list[dict]:
    params = {"withRole": "true"}
    if parent:
        params["parent"] = parent
    return [_project_space(s) for s in client.get("/shared_spaces", params=params) or []]


def get_space(client: LinShareClient, uuid: str) -> dict:
    s = client.get(f"/shared_spaces/{uuid}") or {}
    return {"uuid": s.get("uuid") or uuid, "name": s.get("name"), "type": _type(s)}


def create_space(client: LinShareClient, name: str, workspace: bool = False, parent: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise UsageError("A name is required")
    if workspace and parent:
        raise UsageError("A workspace cannot be inside another workspace: use --in only for a workgroup")
    body: dict = {"name": name, ("sharedSpaceType" if is_v7(client) else "nodeType"): WORK_SPACE if workspace else WORK_GROUP}
    if parent:
        body["parentUuid"] = parent
    return _project_space(client.post_json("/shared_spaces", body) or {})


def delete_spaces(client: LinShareClient, uuids: list[str]) -> list[dict]:
    return delete_each(client, "/shared_spaces/{uuid}", uuids, "shared space")


# -- content --------------------------------------------------------------------------


def _project_node(n: dict) -> dict:
    return {
        "uuid": n.get("uuid"),
        "name": n.get("name"),
        "type": n.get("type"),
        "size": n.get("size"),
        "modificationDate": iso_from_ms(n.get("modificationDate")),
        "lastAuthor": (n.get("lastAuthor") or {}).get("mail"),
    }


def _children(client: LinShareClient, space: str, folder: str | None) -> list[dict]:
    nodes = client.get(f"/shared_spaces/{space}/nodes", params={"parent": folder or space}) or []
    if folder is None and len(nodes) == 1 and nodes[0].get("type") == "ROOT_FOLDER":
        nodes = client.get(f"/shared_spaces/{space}/nodes", params={"parent": nodes[0]["uuid"]}) or []
    return nodes


def list_files(client: LinShareClient, space: str, folder: str | None = None) -> list[dict]:
    nodes = [_project_node(n) for n in _children(client, space, folder)]
    return sorted(nodes, key=lambda n: (n["type"] != "FOLDER", (n["name"] or "").lower()))


def make_folder(client: LinShareClient, space: str, name: str, folder: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise UsageError("A folder name is required")
    body = {"name": name, "type": "FOLDER", "parent": folder or space}
    return _project_node(client.post_json(f"/shared_spaces/{space}/nodes", body) or {})


def download_file(client: LinShareClient, space: str, uuid: str, out_dir: Path) -> dict:
    if not out_dir.is_dir():
        raise UsageError(f"Not a directory: '{out_dir}'")
    n = client.get(f"/shared_spaces/{space}/nodes/{uuid}") or {}
    if n.get("type") and n["type"] != "DOCUMENT":
        raise UsageError(f"'{n.get('name')}' is a folder: download the files in it one by one")
    target = client.download(
        f"/shared_spaces/{space}/nodes/{uuid}/download", safe_target_path(out_dir, n.get("name") or uuid)
    )
    return {"uuid": uuid, "path": str(target)}


def delete_files(client: LinShareClient, space: str, uuids: list[str]) -> list[dict]:
    return delete_each(client, f"/shared_spaces/{space}/nodes/{{uuid}}", uuids, "item")


# -- upload ---------------------------------------------------------------------------


def plan_upload(client: LinShareClient, space: str, files: list[Path], folder: str | None = None) -> dict:
    """What an upload would do, and who will see the files. Reads only."""
    checked = [check_readable_file(Path(f)) for f in files]
    plan = {
        "space": get_space(client, space),
        "folder": folder,
        "files": [{"path": str(f), "name": f.name, "size": f.stat().st_size} for f in checked],
        "visibleTo": None,
        "note": "",
    }
    try:
        plan["visibleTo"] = [m["email"] for m in list_members(client, space)]
    except ServerError as err:
        plan["note"] = f"{err.message}: the members could not be listed"
    return plan


def upload_files(client: LinShareClient, space: str, files: list[Path], folder: str | None = None) -> list[dict]:
    done: list[dict] = []
    for f in files:
        path = check_readable_file(Path(f))
        try:
            done.append(_upload_one(client, space, path, folder))
        except LinShareError as err:
            if done:
                err.message += f" (already uploaded: {', '.join(d['name'] for d in done)})"
            raise
    return done


def _upload_one(client: LinShareClient, space: str, path: Path, folder: str | None) -> dict:
    before = {n.get("uuid"): n.get("modificationDate") for n in _children(client, space, folder)}
    fields = {"workGroupUuid": space}
    if folder:
        fields["workGroupParentNodeUuid"] = folder
    client.upload_flow(path, extra_fields=fields, require_entry=False)
    after = [n for n in _children(client, space, folder) if n.get("type") == "DOCUMENT"]
    stem, suffix = path.stem, path.suffix
    new = [n for n in after if n.get("uuid") not in before and _same_name(n.get("name") or "", path.name, stem, suffix)]
    if new:
        n = max(new, key=lambda n: n.get("modificationDate") or 0)
        return {"uuid": n.get("uuid"), "name": n.get("name"), "size": n.get("size")}
    revised = [n for n in after if n.get("name") == path.name and before.get(n.get("uuid")) != n.get("modificationDate")]
    if revised:
        n = revised[0]
        return {"uuid": n.get("uuid"), "name": n.get("name"), "size": n.get("size"), "newVersion": True}
    raise ServerError(
        f"'{path.name}' was sent but LinShare does not show it yet:"
        f" check `linshare spaces files {space}` before sending it again"
    )


def _same_name(name: str, original: str, stem: str, suffix: str) -> bool:
    # 7.0 renames a duplicate "a.txt" to "a (1).txt"
    return name == original or (name.startswith(stem + " (") and name.endswith(")" + suffix))


# -- members --------------------------------------------------------------------------


def _project_member(m: dict) -> dict:
    account = m.get("account") or {}
    return {
        "uuid": m.get("uuid"),
        "email": account.get("mail"),
        "name": account.get("name"),
        "role": (m.get("role") or {}).get("name"),
    }


def list_members(client: LinShareClient, space: str) -> list[dict]:
    try:
        members = client.get(f"/shared_spaces/{space}/members") or []
    except LinShareError as err:
        if is_v7(client):
            raise ServerError(MEMBERS_BUG_7, http_status=err.http_status) from None
        raise
    return [_project_member(m) for m in members]


def _roles(client: LinShareClient, space_type: str) -> dict[str, dict]:
    params = {"nodeType": space_type} if space_type == WORK_SPACE else None
    roles = client.get("/shared_space_roles", params=params) or []
    # 7.0 returns every role whatever the type: keep the ones for this kind of space
    wanted = (lambda n: n.startswith(WS_PREFIX)) if space_type == WORK_SPACE else (lambda n: not n.startswith(WS_PREFIX))
    return {r["name"]: r for r in roles if r.get("name") and wanted(r["name"])}


def _pick_role(client: LinShareClient, space: dict, role: str) -> dict:
    roles = _roles(client, space["type"] or WORK_GROUP)
    asked = (role or "").strip().upper()
    for candidate in (asked, WS_PREFIX + asked):
        if candidate in roles:
            return roles[candidate]
    valid = ", ".join(sorted(n.removeprefix(WS_PREFIX) for n in roles))
    raise UsageError(f"Unknown role '{role}' for this {space['type'] or 'space'}: use one of {valid}")


def _find_person(client: LinShareClient, space: str, email: str) -> dict:
    if is_v7(client):
        params = {"type": "WORKGROUP_MEMBERS", "workGroupUuid": space}
    else:
        params = {"type": "THREAD_MEMBERS", "threadUuid": space}
    found = [r for r in client.get(f"/autocomplete/{quote(email, safe='')}", params=params) or [] if (r.get("mail") or "").lower() == email]
    if not found:
        raise UsageError(f"No LinShare user with the email {email} (guests must exist before they can be added)")
    return found[0]


def plan_add_member(client: LinShareClient, space: str, email: str, role: str) -> dict:
    """Who would be added, with which role. Reads only."""
    emails = normalize_emails([email])
    target = get_space(client, space)
    picked = _pick_role(client, target, role)
    if target["type"] == WORK_SPACE and is_v7(client):
        raise ServerError(
            "LinShare 7.0 cannot add members to a workspace through the user API (its user search only"
            " accepts a workgroup); you can still add them to the workgroups inside it"
        )
    person = _find_person(client, space, emails[0])
    name = " ".join(p for p in (person.get("firstName"), person.get("lastName")) if p) or None
    return {
        "space": target,
        "email": emails[0],
        "name": name,
        "accountUuid": person.get("userUuid") or person.get("accountUuid"),
        "role": picked["name"],
        "roleUuid": picked["uuid"],
        "alreadyMember": bool(person.get("member")),
    }


def add_member(client: LinShareClient, plan: dict) -> dict:
    if plan.get("alreadyMember"):
        raise UsageError(f"{plan['email']} is already a member of '{plan['space']['name']}': use set-role to change the role")
    body = {
        "account": {"uuid": plan["accountUuid"]},
        "role": {"uuid": plan["roleUuid"], "name": plan["role"]},
        "node": {"uuid": plan["space"]["uuid"]},
    }
    return _project_member(client.post_json(f"/shared_spaces/{plan['space']['uuid']}/members", body) or {})


def _find_member(client: LinShareClient, space: str, who: str) -> dict:
    """`who` is an email (needs the members list, broken on 7.0) or a member id."""
    if "@" not in who:
        return client.get(f"/shared_spaces/{space}/members/{who}") or {}
    email = normalize_emails([who])[0]
    try:
        members = client.get(f"/shared_spaces/{space}/members") or []
    except LinShareError as err:
        if is_v7(client):
            raise ServerError(
                f"{MEMBERS_BUG_7}. Use the member id instead of the email (add-member prints it)",
                http_status=err.http_status,
            ) from None
        raise
    for m in members:
        if ((m.get("account") or {}).get("mail") or "").lower() == email:
            return m
    raise UsageError(f"{email} is not a member of this shared space")


def remove_member(client: LinShareClient, space: str, who: str) -> dict:
    m = _find_member(client, space, who)
    client.delete(f"/shared_spaces/{space}/members/{m['uuid']}")
    return {"uuid": m["uuid"], "email": (m.get("account") or {}).get("mail"), "removed": True}


def set_role(client: LinShareClient, space: str, who: str, role: str) -> dict:
    picked = _pick_role(client, get_space(client, space), role)
    m = _find_member(client, space, who)
    body = {**m, "role": {"uuid": picked["uuid"], "name": picked["name"]}}
    return _project_member(client.put(f"/shared_spaces/{space}/members/{m['uuid']}", body) or {})
