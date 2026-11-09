"""End-to-end tests against a real LinShare server, through the installed `linshare` binary.

Skipped unless LINSHARE_E2E_SERVER is set. See TEST.md.
"""

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

SERVER = os.environ.get("LINSHARE_E2E_SERVER")
EMAIL = os.environ.get("LINSHARE_E2E_EMAIL")
PASSWORD = os.environ.get("LINSHARE_E2E_PASSWORD")
RECIPIENT = os.environ.get("LINSHARE_E2E_RECIPIENT")
RECIPIENT_PASSWORD = os.environ.get("LINSHARE_E2E_RECIPIENT_PASSWORD", PASSWORD)

pytestmark = pytest.mark.skipif(not SERVER, reason="LINSHARE_E2E_SERVER not set")


def _binary() -> str:
    found = shutil.which("linshare")
    assert found, "the `linshare` command is not installed (pip install -e agent-harness)"
    return found


def run(*args, stdin: str | None = None, expect: int = 0) -> dict | list:
    proc = subprocess.run(
        [_binary(), "--json", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
        env=os.environ.copy(),
    )
    assert proc.returncode == expect, f"{args} → exit {proc.returncode}\nstdout:{proc.stdout}\nstderr:{proc.stderr}"
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


@pytest.fixture(autouse=True)
def e2e_env(isolated_config, monkeypatch):
    # conftest already points LINSHARE_CONFIG_DIR at a temp dir; make it visible to the subprocess
    monkeypatch.setenv("LINSHARE_CONFIG_DIR", str(isolated_config))


def test_not_logged_in_is_exit_2():
    out = run("shares", "list", expect=2)
    assert out["error"]["code"] == "auth"


def test_wrong_password_is_exit_2():
    out = run("login", "--server", SERVER, "--email", EMAIL, stdin="definitely-wrong\n", expect=2)
    assert out["error"]["code"] in ("auth", "totp_required")


def test_full_flow(tmp_path):
    tag = uuid.uuid4().hex[:8]
    doc = tmp_path / f"e2e été {tag}.txt"
    doc.write_text(f"linshare-cli e2e {tag}\n", encoding="utf-8")

    login = run("login", "--server", SERVER, "--email", EMAIL, stdin=f"{PASSWORD}\n")
    assert login["email"] == EMAIL and login["tokenUuid"]

    me = run("whoami")
    assert me["email"] == EMAIL
    assert me["serverVersion"].startswith(("6.5", "7."))

    names_before = {d["name"] for d in run("documents", "list")}
    plan = run("share", str(doc), "--to", RECIPIENT, "--to", RECIPIENT.upper(), "--expires", "7d", "--dry-run")
    assert plan["dryRun"] is True
    assert plan["recipients"] == [{"email": RECIPIENT.lower(), "external": False}]
    assert set(plan["downloadAlert"]) == {"enabled", "date", "detail"}
    assert isinstance(plan["copyToMe"], bool)
    assert {d["name"] for d in run("documents", "list")} == names_before, "dry-run must not upload"

    shared = run("share", str(doc), "--to", RECIPIENT, "--expires", "7d", "--message", f"e2e {tag}")
    assert len(shared["shared"]) == 1
    share_id = shared["shared"][0]["uuid"]
    assert shared["shared"][0]["documentName"] == doc.name
    assert shared["shared"][0]["recipient"] == RECIPIENT.lower()

    assert share_id in {s["uuid"] for s in run("shares", "list")}
    assert run("shares", "revoke", share_id) == [{"uuid": share_id, "deleted": True}]
    assert share_id not in {s["uuid"] for s in run("shares", "list")}

    doc_ids = [d["uuid"] for d in run("documents", "list") if d["name"] == doc.name]
    assert len(doc_ids) == 1
    run("documents", "delete", doc_ids[0])
    assert doc.name not in {d["name"] for d in run("documents", "list")}

    req_plan = run("request-files", "--from", RECIPIENT, "--label", f"e2e-{tag}", "--expires", "7d", "--message", "please", "--dry-run")
    assert req_plan["collective"] is False
    req = run("request-files", "--from", RECIPIENT, "--label", f"e2e-{tag}", "--expires", "7d", "--message", "please")
    listed = {r["uuid"]: r for r in run("requests", "list")}
    assert req["uuid"] in listed
    assert run("requests", "close", req["uuid"]) == [{"uuid": req["uuid"], "status": "CLOSED"}]

    out = run("logout")
    assert out["revokedOnServer"] is True
    run("whoami", expect=2)


def test_recipient_downloads_a_fresh_share(tmp_path):
    """The first download of a share is the one that emails the sender, so share a new file
    every run instead of downloading whatever the recipient already has."""
    tag = uuid.uuid4().hex[:8]
    doc = tmp_path / f"e2e received {tag}.txt"
    content = f"linshare-cli e2e received {tag}\n"
    doc.write_text(content, encoding="utf-8")

    run("login", "--server", SERVER, "--email", EMAIL, stdin=f"{PASSWORD}\n")
    share_id = run("share", str(doc), "--to", RECIPIENT, "--expires", "7d")["shared"][0]["uuid"]
    run("logout")
    try:
        run("login", "--server", SERVER, "--email", RECIPIENT, stdin=f"{RECIPIENT_PASSWORD}\n")
        try:
            received = [r for r in run("received", "list") if r["name"] == doc.name]
            assert len(received) == 1, f"{doc.name} not in the recipient's received list"
            out = tmp_path / "dl"
            out.mkdir()
            path = Path(run("received", "download", received[0]["uuid"], "--out", str(out))["path"])
            assert path.parent == out
            assert path.read_text(encoding="utf-8") == content
        finally:
            run("logout")
    finally:
        run("login", "--server", SERVER, "--email", EMAIL, stdin=f"{PASSWORD}\n")
        run("shares", "revoke", share_id)
        for d in run("documents", "list"):
            if d["name"] == doc.name:
                run("documents", "delete", d["uuid"])
        run("logout")


def test_large_file_is_uploaded_in_chunks(tmp_path):
    """Above 2 MiB the file goes through the chunked "flow" upload of the web UI."""
    tag = uuid.uuid4().hex[:8]
    doc = tmp_path / f"e2e grand été {tag}.bin"
    size = 5 * 1024 * 1024 + 123
    doc.write_bytes(os.urandom(size))

    run("login", "--server", SERVER, "--email", EMAIL, stdin=f"{PASSWORD}\n")
    try:
        shared = run("share", str(doc), "--to", RECIPIENT, "--expires", "7d")
        share_id = shared["shared"][0]["uuid"]
        assert shared["shared"][0]["documentName"] == doc.name
        stored = [d for d in run("documents", "list") if d["name"] == doc.name]
        assert len(stored) == 1 and stored[0]["size"] == size
        run("shares", "revoke", share_id)
        run("documents", "delete", stored[0]["uuid"])
    finally:
        run("logout")


def test_workgroup_lifecycle(tmp_path):
    """Create a workgroup, fill it, share it with the recipient, then delete it all."""
    tag = uuid.uuid4().hex[:8]
    run("login", "--server", SERVER, "--email", EMAIL, stdin=f"{PASSWORD}\n")
    space_id = None
    try:
        space = run("spaces", "create", f"e2e équipe {tag}")
        space_id = space["uuid"]
        assert space["type"] == "WORK_GROUP"
        assert space_id in {s["uuid"] for s in run("spaces", "list")}

        folder = run("spaces", "mkdir", space_id, "dossier été")
        small = tmp_path / f"petit été {tag}.txt"
        small.write_text(f"workgroup {tag}\n", encoding="utf-8")
        big = tmp_path / "gros.bin"
        big.write_bytes(os.urandom(5 * 1024 * 1024 + 7))

        plan = run("spaces", "upload", space_id, str(small), "--folder", folder["uuid"], "--dry-run")
        assert plan["dryRun"] is True and plan["files"][0]["name"] == small.name
        assert run("spaces", "files", space_id, "--folder", folder["uuid"]) == [], "dry-run must not upload"

        up = run("spaces", "upload", space_id, str(small), str(big), "--folder", folder["uuid"])["uploaded"]
        assert [u["name"] for u in up] == [small.name, big.name]
        assert [u["size"] for u in up] == [small.stat().st_size, big.stat().st_size]
        top = run("spaces", "files", space_id)
        assert [(f["type"], f["name"]) for f in top] == [("FOLDER", "dossier été")]
        inside = {f["name"]: f for f in run("spaces", "files", space_id, "--folder", folder["uuid"])}
        assert set(inside) == {small.name, big.name}

        out = tmp_path / "dl"
        out.mkdir()
        path = Path(run("spaces", "download", space_id, up[0]["uuid"], "--out", str(out))["path"])
        assert path.read_text(encoding="utf-8") == small.read_text(encoding="utf-8")

        add_plan = run("spaces", "add-member", space_id, RECIPIENT, "--role", "reader", "--dry-run")
        assert add_plan["dryRun"] is True and add_plan["role"] == "READER" and add_plan["alreadyMember"] is False
        member = run("spaces", "add-member", space_id, RECIPIENT, "--role", "reader")
        assert member["email"] == RECIPIENT.lower() and member["role"] == "READER"

        listed = subprocess.run([_binary(), "--json", "spaces", "members", space_id], capture_output=True, text=True,
                                timeout=120, env=os.environ.copy())
        if listed.returncode == 0:  # 6.5; 7.0 cannot list members (server bug, see TEST.md)
            emails = {m["email"]: m["role"] for m in json.loads(listed.stdout)}
            assert emails[RECIPIENT.lower()] == "READER"
        else:
            assert "cannot list the members" in json.loads(listed.stdout)["error"]["message"]

        assert run("spaces", "set-role", space_id, member["uuid"], "writer")["role"] == "WRITER"
        assert run("spaces", "remove-member", space_id, member["uuid"])["removed"] is True

        run("spaces", "delete-files", space_id, up[0]["uuid"])
        assert {f["name"] for f in run("spaces", "files", space_id, "--folder", folder["uuid"])} == {big.name}
    finally:
        if space_id:
            run("spaces", "delete", space_id)
            assert space_id not in {s["uuid"] for s in run("spaces", "list")}
        run("logout")
