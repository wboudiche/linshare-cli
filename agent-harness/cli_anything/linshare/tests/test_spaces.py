"""Shared spaces: workgroups and workspaces (user API v5).

The shapes below are the ones the two test servers really return (see TEST.md):
6.5 says `nodeType` and has a ROOT_FOLDER node; 7.0 says `sharedSpaceType`, uses role
names as role uuids, and some of its member endpoints are broken."""

import json

import pytest
import responses
from click.testing import CliRunner

from cli_anything.linshare.core import spaces
from cli_anything.linshare.core.config import Config, save_config
from cli_anything.linshare.core.errors import ServerError, UsageError
from cli_anything.linshare.linshare_cli import cli
from cli_anything.linshare.utils.linshare_backend import API_PATH, LinShareClient

SERVER = "https://ls.example.com"
BASE = SERVER + API_PATH
W = "wg-1"
MiB = 1024 * 1024


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    from cli_anything.linshare.utils import linshare_backend

    monkeypatch.setattr(linshare_backend.time, "sleep", lambda s: None)


def client():
    return LinShareClient(SERVER, token="t")


def version(v):
    responses.get(BASE + "/authentication/version", json={"version": v})


def space(type_="WORK_GROUP", v7=False, uuid=W, name="Team", role="ADMIN", parent=None):
    key = "sharedSpaceType" if v7 else "nodeType"
    return {"uuid": uuid, "name": name, key: type_, "parentUuid": parent,
            "role": {"uuid": role if v7 else "r-" + role, "name": role}, "modificationDate": 1794000000000}


def node(uuid, name, type_="DOCUMENT", size=9, modified=1794000000000, parent="root"):
    return {"uuid": uuid, "name": name, "type": type_, "size": size, "parent": parent,
            "modificationDate": modified, "lastAuthor": {"mail": "amy@x.org"}}


ROLES_6 = [{"uuid": "r-" + n, "name": n, "type": "WORK_GROUP"} for n in ("ADMIN", "CONTRIBUTOR", "WRITER", "READER")]
WS_ROLES_6 = [{"uuid": "r-" + n, "name": n, "type": "WORK_SPACE"} for n in ("WORK_SPACE_ADMIN", "WORK_SPACE_WRITER", "WORK_SPACE_READER")]
ROLES_7 = [{"uuid": n, "name": n} for n in ("ADMIN", "WORK_SPACE_WRITER", "READER", "CONTRIBUTOR", "WORK_SPACE_ADMIN", "WORK_SPACE_READER", "WRITER")]


# -- list / create / delete -------------------------------------------------------


@responses.activate
@pytest.mark.parametrize("v7", [False, True])
def test_list_spaces_same_shape_on_both_versions(v7):
    responses.get(BASE + "/shared_spaces", json=[space(v7=v7), space("WORK_SPACE", v7, "ws-1", "Drive", "WORK_SPACE_ADMIN")])
    out = spaces.list_spaces(client())
    assert out == [
        {"uuid": W, "name": "Team", "type": "WORK_GROUP", "role": "ADMIN", "parentUuid": None,
         "modificationDate": "2026-11-06T21:20:00Z"},
        {"uuid": "ws-1", "name": "Drive", "type": "WORK_SPACE", "role": "WORK_SPACE_ADMIN", "parentUuid": None,
         "modificationDate": "2026-11-06T21:20:00Z"},
    ]
    assert responses.calls[0].request.params == {"withRole": "true"}


@responses.activate
def test_list_spaces_inside_a_workspace():
    responses.get(BASE + "/shared_spaces", json=[])
    spaces.list_spaces(client(), parent="ws-1")
    assert responses.calls[0].request.params == {"withRole": "true", "parent": "ws-1"}


@responses.activate
@pytest.mark.parametrize("v, key", [("6.5.5", "nodeType"), ("7.0.0-SNAPSHOT", "sharedSpaceType")])
def test_create_uses_the_field_name_of_the_server(v, key):
    version(v)
    responses.post(BASE + "/shared_spaces", json=space(v7=key == "sharedSpaceType", name="New"))
    out = spaces.create_space(client(), "New")
    assert json.loads(responses.calls[-1].request.body) == {"name": "New", key: "WORK_GROUP"}
    assert out["name"] == "New" and out["type"] == "WORK_GROUP"


@responses.activate
def test_create_workspace_and_workgroup_inside_it():
    version("6.5.5")
    responses.post(BASE + "/shared_spaces", json=space("WORK_SPACE"))
    spaces.create_space(client(), "Drive", workspace=True)
    assert json.loads(responses.calls[-1].request.body) == {"name": "Drive", "nodeType": "WORK_SPACE"}
    responses.post(BASE + "/shared_spaces", json=space(parent="ws-1"))
    spaces.create_space(client(), "Sub", parent="ws-1")
    assert json.loads(responses.calls[-1].request.body) == {"name": "Sub", "nodeType": "WORK_GROUP", "parentUuid": "ws-1"}


def test_create_rejects_a_workspace_inside_a_workspace():
    with pytest.raises(UsageError):
        spaces.create_space(client(), "x", workspace=True, parent="ws-1")


def test_create_rejects_an_empty_name():
    with pytest.raises(UsageError):
        spaces.create_space(client(), "  ")


@responses.activate
def test_delete_spaces():
    responses.delete(BASE + f"/shared_spaces/{W}", json=space())
    assert spaces.delete_spaces(client(), [W]) == [{"uuid": W, "deleted": True}]


# -- content ----------------------------------------------------------------------


@responses.activate
def test_files_at_the_root_on_7_0():
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[node("d1", "a.txt"), node("f1", "sub", "FOLDER", None)])
    out = spaces.list_files(client(), W)
    assert [(f["uuid"], f["type"], f["name"]) for f in out] == [("f1", "FOLDER", "sub"), ("d1", "DOCUMENT", "a.txt")]
    assert responses.calls[0].request.params == {"parent": W}


@responses.activate
def test_files_at_the_root_on_6_5_go_through_the_root_folder():
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[node("root", "Team", "ROOT_FOLDER", None)])
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[node("d1", "a.txt")])
    out = spaces.list_files(client(), W)
    assert [f["uuid"] for f in out] == ["d1"]
    assert [c.request.params for c in responses.calls] == [{"parent": W}, {"parent": "root"}]


@responses.activate
def test_files_in_a_folder():
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[node("d2", "b.txt")])
    spaces.list_files(client(), W, folder="f1")
    assert responses.calls[0].request.params == {"parent": "f1"}


@responses.activate
def test_mkdir():
    responses.post(BASE + f"/shared_spaces/{W}/nodes", json=node("f2", "été", "FOLDER", None))
    out = spaces.make_folder(client(), W, "été")
    assert json.loads(responses.calls[0].request.body) == {"name": "été", "type": "FOLDER", "parent": W}
    assert out["uuid"] == "f2" and out["type"] == "FOLDER"
    responses.post(BASE + f"/shared_spaces/{W}/nodes", json=node("f3", "x", "FOLDER", None))
    spaces.make_folder(client(), W, "x", folder="f2")
    assert json.loads(responses.calls[-1].request.body)["parent"] == "f2"


@responses.activate
def test_download(tmp_path):
    responses.get(BASE + f"/shared_spaces/{W}/nodes/d1", json=node("d1", "a.txt"))
    responses.get(BASE + f"/shared_spaces/{W}/nodes/d1/download", body=b"hello")
    out = spaces.download_file(client(), W, "d1", tmp_path)
    assert out == {"uuid": "d1", "path": str(tmp_path / "a.txt")}
    assert (tmp_path / "a.txt").read_bytes() == b"hello"


@responses.activate
def test_download_refuses_a_folder(tmp_path):
    responses.get(BASE + f"/shared_spaces/{W}/nodes/f1", json=node("f1", "sub", "FOLDER", None))
    with pytest.raises(UsageError, match="folder"):
        spaces.download_file(client(), W, "f1", tmp_path)


@responses.activate
def test_delete_files():
    responses.delete(BASE + f"/shared_spaces/{W}/nodes/d1", json=node("d1", "a.txt"))
    assert spaces.delete_files(client(), W, ["d1"]) == [{"uuid": "d1", "deleted": True}]


# -- upload -----------------------------------------------------------------------


def parts(request) -> dict:
    import email
    import email.policy

    raw = b"Content-Type: " + request.headers["Content-Type"].encode() + b"\r\n\r\n" + request.body
    msg = email.message_from_bytes(raw, policy=email.policy.HTTP)
    return {p.get_param("name", header="content-disposition"): p.get_payload(decode=True) for p in msg.iter_parts()}


FLOW_DONE = {"chunkUploadSuccess": True, "lastChunk": True, "entry": {"uuid": None, "name": None}}


@responses.activate
def test_upload_goes_through_flow_with_the_workgroup_fields(tmp_path):
    # the simple multipart upload fails on 7.0; flow works on both, and gives no uuid back
    f = tmp_path / "été.txt"
    f.write_text("hi")
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[node("old", "other.txt")])
    responses.post(BASE + "/flow", json=FLOW_DONE)
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[node("old", "other.txt"), node("new", "été.txt", size=2)])
    out = spaces.upload_files(client(), W, [f], folder="f1")
    sent = parts(next(c.request for c in responses.calls if c.request.url.endswith("/flow")))
    assert sent["workGroupUuid"] == W.encode() and sent["workGroupParentNodeUuid"] == b"f1"
    assert sent["flowFilename"] == "été.txt".encode()
    assert out == [{"uuid": "new", "name": "été.txt", "size": 2}]


@responses.activate
def test_upload_at_the_root_sends_no_parent(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[])
    responses.post(BASE + "/flow", json=FLOW_DONE)
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[node("new", "a.txt", size=2)])
    spaces.upload_files(client(), W, [f])
    sent = parts(next(c.request for c in responses.calls if c.request.url.endswith("/flow")))
    assert "workGroupParentNodeUuid" not in sent


@responses.activate
def test_upload_same_name_on_7_0_is_renamed(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[node("d1", "a.txt")])
    responses.post(BASE + "/flow", json=FLOW_DONE)
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[node("d1", "a.txt"), node("d2", "a (1).txt", size=2)])
    assert spaces.upload_files(client(), W, [f]) == [{"uuid": "d2", "name": "a (1).txt", "size": 2}]


@responses.activate
def test_upload_same_name_on_6_5_is_a_new_version(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[node("d1", "a.txt", modified=1)])
    responses.post(BASE + "/flow", json=FLOW_DONE)
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[node("d1", "a.txt", size=2, modified=2)])
    assert spaces.upload_files(client(), W, [f]) == [{"uuid": "d1", "name": "a.txt", "size": 2, "newVersion": True}]


@responses.activate
def test_large_upload_is_chunked(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * (5 * MiB))
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[])
    responses.post(BASE + "/flow", json={"chunkUploadSuccess": True, "lastChunk": False})
    responses.post(BASE + "/flow", json={"chunkUploadSuccess": True, "lastChunk": False})
    responses.post(BASE + "/flow", json=FLOW_DONE)
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[node("new", "big.bin", size=5 * MiB)])
    spaces.upload_files(client(), W, [f])
    flows = [parts(c.request) for c in responses.calls if c.request.url.endswith("/flow")]
    assert [p["flowChunkNumber"] for p in flows] == [b"1", b"2", b"3"]
    assert {p["workGroupUuid"] for p in flows} == {W.encode()}


@responses.activate
def test_upload_not_found_afterwards_is_reported_not_hidden(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[])
    responses.post(BASE + "/flow", json=FLOW_DONE)
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[])
    with pytest.raises(ServerError, match="check `linshare spaces files"):
        spaces.upload_files(client(), W, [f])


@responses.activate
def test_upload_refused_chunk_stops(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    responses.get(BASE + f"/shared_spaces/{W}/nodes", json=[])
    responses.post(BASE + "/flow", json={"chunkUploadSuccess": False, "errCode": 46010, "errorMessage": "quota"})
    from cli_anything.linshare.core.errors import BusinessError

    with pytest.raises(BusinessError):
        spaces.upload_files(client(), W, [f])


# -- members ---------------------------------------------------------------------


def member(uuid, mail, role, v7=False):
    return {"uuid": uuid, "account": {"uuid": "acc-" + mail, "mail": mail, "name": mail.split("@")[0]},
            "role": {"uuid": role if v7 else "r-" + role, "name": role}, "node": {"uuid": W}}


@responses.activate
def test_members():
    responses.get(BASE + f"/shared_spaces/{W}/members", json=[member("m1", "amy@x.org", "ADMIN"), member("m2", "bob@x.org", "READER")])
    assert spaces.list_members(client(), W) == [
        {"uuid": "m1", "email": "amy@x.org", "name": "amy", "role": "ADMIN"},
        {"uuid": "m2", "email": "bob@x.org", "name": "bob", "role": "READER"},
    ]


@responses.activate
def test_members_on_7_0_explains_the_server_bug():
    responses.get(BASE + f"/shared_spaces/{W}/members", status=400, json={"message": "User uuid must be set.", "errCode": 0})
    version("7.0.0-SNAPSHOT")
    with pytest.raises(ServerError, match="LinShare 7.0 cannot list"):
        spaces.list_members(client(), W)


@responses.activate
def test_plan_add_member_6_5():
    version("6.5.5")
    responses.get(BASE + f"/shared_spaces/{W}", json=space())
    responses.get(BASE + "/shared_space_roles", json=ROLES_6)
    responses.get(BASE + "/autocomplete/bob%40x.org", json=[
        {"type": "threadmember", "userUuid": "acc-bob", "mail": "bob@x.org", "firstName": "Bob", "lastName": "B", "member": False}])
    plan = spaces.plan_add_member(client(), W, "Bob@X.org", "reader")
    assert plan == {"space": {"uuid": W, "name": "Team", "type": "WORK_GROUP"}, "email": "bob@x.org",
                    "name": "Bob B", "accountUuid": "acc-bob", "role": "READER", "roleUuid": "r-READER",
                    "alreadyMember": False}
    ac = next(c.request for c in responses.calls if "/autocomplete/" in c.request.url)
    assert ac.params == {"type": "THREAD_MEMBERS", "threadUuid": W}
    assert all(c.request.method == "GET" for c in responses.calls)


@responses.activate
def test_plan_add_member_7_0_uses_the_other_search_type():
    version("7.0.0")
    responses.get(BASE + f"/shared_spaces/{W}", json=space(v7=True))
    responses.get(BASE + "/shared_space_roles", json=ROLES_7)
    responses.get(BASE + "/autocomplete/bob%40x.org", json=[{"type": "threadmember", "userUuid": "acc-bob", "mail": "bob@x.org"}])
    plan = spaces.plan_add_member(client(), W, "bob@x.org", "WRITER")
    ac = next(c.request for c in responses.calls if "/autocomplete/" in c.request.url)
    assert ac.params == {"type": "WORKGROUP_MEMBERS", "workGroupUuid": W}
    assert plan["roleUuid"] == "WRITER"


@responses.activate
def test_plan_add_member_unknown_person():
    version("6.5.5")
    responses.get(BASE + f"/shared_spaces/{W}", json=space())
    responses.get(BASE + "/shared_space_roles", json=ROLES_6)
    responses.get(BASE + "/autocomplete/nobody%40x.org", json=[])
    with pytest.raises(UsageError, match="No LinShare user"):
        spaces.plan_add_member(client(), W, "nobody@x.org", "READER")


@responses.activate
def test_plan_add_member_bad_role_lists_the_valid_ones():
    version("7.0.0")
    responses.get(BASE + f"/shared_spaces/{W}", json=space(v7=True))
    responses.get(BASE + "/shared_space_roles", json=ROLES_7)
    with pytest.raises(UsageError, match="ADMIN, CONTRIBUTOR, READER, WRITER"):
        spaces.plan_add_member(client(), W, "bob@x.org", "OWNER")


@responses.activate
def test_workspace_roles_take_short_names():
    version("6.5.5")
    responses.get(BASE + "/shared_spaces/ws-1", json=space("WORK_SPACE", uuid="ws-1"))
    responses.get(BASE + "/shared_space_roles", json=WS_ROLES_6)
    responses.get(BASE + "/autocomplete/bob%40x.org", json=[{"type": "threadmember", "userUuid": "acc-bob", "mail": "bob@x.org"}])
    plan = spaces.plan_add_member(client(), "ws-1", "bob@x.org", "writer")
    assert plan["role"] == "WORK_SPACE_WRITER"
    roles_call = next(c.request for c in responses.calls if "/shared_space_roles" in c.request.url)
    assert roles_call.params == {"nodeType": "WORK_SPACE"}


@responses.activate
def test_add_member_sends_role_name_and_uuid():
    responses.post(BASE + f"/shared_spaces/{W}/members", json=member("m2", "bob@x.org", "READER"))
    plan = {"space": {"uuid": W, "name": "Team", "type": "WORK_GROUP"}, "email": "bob@x.org", "name": "Bob",
            "accountUuid": "acc-bob", "role": "READER", "roleUuid": "r-READER", "alreadyMember": False}
    out = spaces.add_member(client(), plan)
    assert json.loads(responses.calls[0].request.body) == {
        "account": {"uuid": "acc-bob"}, "role": {"uuid": "r-READER", "name": "READER"}, "node": {"uuid": W}}
    assert out == {"uuid": "m2", "email": "bob@x.org", "name": "bob", "role": "READER"}


def test_add_member_refuses_someone_already_in():
    with pytest.raises(UsageError, match="already a member"):
        spaces.add_member(client(), {"alreadyMember": True, "email": "bob@x.org", "space": {"name": "Team"}})


@responses.activate
def test_remove_member_by_email():
    responses.get(BASE + f"/shared_spaces/{W}/members", json=[member("m2", "bob@x.org", "READER")])
    responses.delete(BASE + f"/shared_spaces/{W}/members/m2", json=member("m2", "bob@x.org", "READER"))
    assert spaces.remove_member(client(), W, "Bob@x.org") == {"uuid": "m2", "email": "bob@x.org", "removed": True}


@responses.activate
def test_remove_member_by_member_id_works_without_the_list():
    # on 7.0 the member list is broken: the id printed by add-member still works
    responses.get(BASE + f"/shared_spaces/{W}/members/m2", json=member("m2", "bob@x.org", "READER", v7=True))
    responses.delete(BASE + f"/shared_spaces/{W}/members/m2", json=member("m2", "bob@x.org", "READER", v7=True))
    assert spaces.remove_member(client(), W, "m2")["email"] == "bob@x.org"


@responses.activate
def test_remove_member_unknown_email():
    responses.get(BASE + f"/shared_spaces/{W}/members", json=[member("m1", "amy@x.org", "ADMIN")])
    with pytest.raises(UsageError, match="not a member"):
        spaces.remove_member(client(), W, "bob@x.org")


@responses.activate
def test_set_role():
    version("6.5.5")
    responses.get(BASE + f"/shared_spaces/{W}", json=space())
    responses.get(BASE + "/shared_space_roles", json=ROLES_6)
    m = member("m2", "bob@x.org", "READER")
    responses.get(BASE + f"/shared_spaces/{W}/members", json=[m])
    responses.put(BASE + f"/shared_spaces/{W}/members/m2", json={**m, "role": {"uuid": "r-WRITER", "name": "WRITER"}})
    out = spaces.set_role(client(), W, "bob@x.org", "writer")
    body = json.loads(responses.calls[-1].request.body)
    assert body["role"] == {"uuid": "r-WRITER", "name": "WRITER"}
    assert out["role"] == "WRITER"


# -- command line ------------------------------------------------------------------


@pytest.fixture
def logged_in():
    save_config(Config(SERVER, "amy@x.org", "t", "u"))


@responses.activate
def test_cli_list(logged_in):
    responses.get(BASE + "/shared_spaces", json=[space()])
    result = CliRunner().invoke(cli, ["--json", "spaces", "list"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["name"] == "Team"


@responses.activate
def test_cli_upload_dry_run_only_reads(logged_in, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    responses.get(BASE + f"/shared_spaces/{W}", json=space())
    responses.get(BASE + f"/shared_spaces/{W}/members", json=[member("m1", "amy@x.org", "ADMIN"), member("m2", "bob@x.org", "READER")])
    result = CliRunner().invoke(cli, ["--json", "spaces", "upload", W, str(f), "--dry-run"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["dryRun"] is True
    assert out["files"] == [{"path": str(f), "name": "a.txt", "size": 2}]
    assert out["space"] == {"uuid": W, "name": "Team", "type": "WORK_GROUP"}
    assert out["visibleTo"] == ["amy@x.org", "bob@x.org"]
    assert {c.request.method for c in responses.calls} == {"GET"}


@responses.activate
def test_cli_upload_dry_run_on_7_0_still_works_without_members(logged_in, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    responses.get(BASE + f"/shared_spaces/{W}", json=space(v7=True))
    responses.get(BASE + f"/shared_spaces/{W}/members", status=400, json={"message": "User uuid must be set."})
    version("7.0.0")
    result = CliRunner().invoke(cli, ["--json", "spaces", "upload", W, str(f), "--dry-run"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["visibleTo"] is None and "cannot list" in out["note"]


def test_cli_upload_missing_file_is_usage(logged_in, tmp_path):
    result = CliRunner().invoke(cli, ["--json", "spaces", "upload", W, str(tmp_path / "nope"), "--dry-run"])
    assert result.exit_code == 1


@responses.activate
def test_cli_add_member_dry_run(logged_in):
    version("6.5.5")
    responses.get(BASE + f"/shared_spaces/{W}", json=space())
    responses.get(BASE + "/shared_space_roles", json=ROLES_6)
    responses.get(BASE + "/autocomplete/bob%40x.org", json=[{"type": "threadmember", "userUuid": "acc-bob", "mail": "bob@x.org"}])
    result = CliRunner().invoke(cli, ["--json", "spaces", "add-member", W, "bob@x.org", "--role", "READER", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dryRun"] is True
    assert {c.request.method for c in responses.calls} == {"GET"}


def test_cli_add_member_requires_a_role(logged_in):
    result = CliRunner().invoke(cli, ["--json", "spaces", "add-member", W, "bob@x.org"])
    assert result.exit_code == 1


@responses.activate
def test_add_member_to_a_workspace_on_7_0_says_why_it_cannot():
    # 7.0 can only look people up for a workgroup ("work group ... not found" for a workspace)
    version("7.0.0")
    responses.get(BASE + "/shared_spaces/ws-1", json=space("WORK_SPACE", v7=True, uuid="ws-1"))
    responses.get(BASE + "/shared_space_roles", json=ROLES_7)
    with pytest.raises(ServerError, match="LinShare 7.0 cannot add members to a workspace"):
        spaces.plan_add_member(client(), "ws-1", "bob@x.org", "WRITER")
    assert not any("/autocomplete/" in c.request.url for c in responses.calls)
