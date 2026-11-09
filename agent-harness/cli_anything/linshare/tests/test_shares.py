import json
from datetime import date

import pytest
import responses
from click.testing import CliRunner

from cli_anything.linshare.core.config import Config, save_config
from cli_anything.linshare.core.errors import BusinessError, UsageError
from cli_anything.linshare.core.shares import (
    execute_share,
    list_shares,
    plan_share,
    revoke_shares,
)
from cli_anything.linshare.linshare_cli import cli
from cli_anything.linshare.utils.linshare_backend import API_PATH, LinShareClient

SERVER = "https://ls.example.com"
BASE = SERVER + API_PATH
TODAY = date(2026, 11, 6)
# 2026-11-13T23:59:59Z
END_OF_13_NOV_MS = 1794614399000


def client():
    return LinShareClient(SERVER, token="t")


@pytest.fixture
def two_files(tmp_path):
    a = tmp_path / "a.pdf"
    a.write_bytes(b"aaa")
    b = tmp_path / "b.txt"
    b.write_bytes(b"bbbbb")
    return [a, b]


def test_plan_share_flags_external_and_dedupes(two_files):
    plan = plan_share(
        two_files[:1],
        ["Marc@Acme.com", "marc@acme.com", "bob@linagora.com"],
        "7d",
        "Contract",
        "Please sign",
        False,
        "me@linagora.com",
        TODAY,
    )
    assert plan["recipients"] == [
        {"email": "marc@acme.com", "external": True},
        {"email": "bob@linagora.com", "external": False},
    ]
    assert plan["files"] == [{"path": str(two_files[0]), "name": "a.pdf", "size": 3}]
    assert plan["expirationDate"] == "2026-11-13"
    assert plan["subject"] == "Contract"
    assert plan["message"] == "Please sign"
    assert plan["passwordLink"] is False
    assert "heuristic" in plan["note"]


def test_plan_share_without_expiry(two_files):
    plan = plan_share(two_files, ["x@y.org"], None, None, None, True, "me@linagora.com", TODAY)
    assert plan["expirationDate"] is None
    assert plan["passwordLink"] is True


def test_plan_share_missing_file_is_usage_error(tmp_path):
    with pytest.raises(UsageError):
        plan_share([tmp_path / "nope"], ["x@y.org"], None, None, None, False, "me@x.org", TODAY)


@responses.activate
def test_dry_run_only_reads(two_files):
    save_config(Config(SERVER, "me@linagora.com", "t", "u"))
    responses.get(BASE + "/functionalities", json=[])
    result = CliRunner().invoke(
        cli, ["--json", "share", str(two_files[0]), "--to", "marc@acme.com", "--expires", "7d", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dryRun"] is True
    assert payload["recipients"] == [{"email": "marc@acme.com", "external": True}]
    assert [c.request.method for c in responses.calls] == ["GET"]  # the admin settings, nothing else


def _upload_responses():
    responses.post(BASE + "/documents", json={"uuid": "d1", "name": "a.pdf", "size": 3})
    responses.post(BASE + "/documents", json={"uuid": "d2", "name": "b.txt", "size": 5})


@responses.activate
def test_execute_share_body(two_files):
    _upload_responses()
    responses.post(
        BASE + "/shares",
        json=[
            {
                "uuid": "s1",
                "name": "a.pdf",
                "recipient": {"mail": "marc@acme.com"},
                "expirationDate": END_OF_13_NOV_MS,
            }
        ],
    )
    plan = plan_share(two_files, ["marc@acme.com"], "2026-11-13", "S", "M", True, "me@linagora.com", TODAY)
    result = execute_share(client(), plan)
    body = json.loads(responses.calls[2].request.body)
    assert body == {
        "documents": ["d1", "d2"],
        "recipients": [{"mail": "marc@acme.com"}],
        "expirationDate": END_OF_13_NOV_MS,
        "subject": "S",
        "message": "M",
        "secured": True,
        "creationAcknowledgement": False,
    }
    assert result["shared"] == [
        {
            "uuid": "s1",
            "documentName": "a.pdf",
            "recipient": "marc@acme.com",
            "expirationDate": "2026-11-13T23:59:59Z",
        }
    ]


@responses.activate
def test_execute_share_omits_empty_optional_fields(two_files):
    _upload_responses()
    responses.post(BASE + "/shares", json=[])
    plan = plan_share(two_files[:1], ["marc@acme.com"], None, None, None, False, "me@linagora.com", TODAY)
    execute_share(client(), plan)
    body = json.loads(responses.calls[1].request.body)
    assert "expirationDate" not in body
    assert "subject" not in body
    assert "message" not in body


@responses.activate
def test_share_failure_deletes_uploaded_documents(two_files):
    _upload_responses()
    responses.post(BASE + "/shares", status=403, json={"errCode": 33423, "message": "forbidden"})
    responses.delete(BASE + "/documents/d1", json={})
    responses.delete(BASE + "/documents/d2", json={})
    plan = plan_share(two_files, ["marc@acme.com"], None, None, None, False, "me@linagora.com", TODAY)
    with pytest.raises(BusinessError) as exc:
        execute_share(client(), plan)
    assert exc.value.exit_code == 3
    assert "uploaded files were removed" in exc.value.message
    deleted = [c.request.url for c in responses.calls if c.request.method == "DELETE"]
    assert deleted == [BASE + "/documents/d1", BASE + "/documents/d2"]


@responses.activate
def test_upload_failure_midway_removes_earlier_uploads(two_files):
    responses.post(BASE + "/documents", json={"uuid": "d1", "name": "a.pdf", "size": 3})
    responses.post(BASE + "/documents", status=403, json={"errCode": 46010, "message": "too big"})
    responses.delete(BASE + "/documents/d1", json={})
    plan = plan_share(two_files, ["marc@acme.com"], None, None, None, False, "me@linagora.com", TODAY)
    with pytest.raises(BusinessError) as exc:
        execute_share(client(), plan)
    assert "uploaded files were removed" in exc.value.message
    assert responses.calls[-1].request.method == "DELETE"


@responses.activate
def test_list_and_revoke_shares():
    responses.get(
        BASE + "/shares",
        json=[
            {
                "uuid": "s1",
                "name": "a.pdf",
                "recipient": {"mail": "marc@acme.com"},
                "creationDate": 1793750400000,
                "expirationDate": None,
                "downloaded": 2,
            }
        ],
    )
    responses.delete(BASE + "/shares/s1", json={})
    assert list_shares(client()) == [
        {
            "uuid": "s1",
            "documentName": "a.pdf",
            "recipient": "marc@acme.com",
            "creationDate": "2026-11-04T00:00:00Z",
            "expirationDate": None,
            "downloaded": 2,
        }
    ]
    assert revoke_shares(client(), ["s1"]) == [{"uuid": "s1", "deleted": True}]


@responses.activate
def test_cli_share_real_run(two_files):
    save_config(Config(SERVER, "me@linagora.com", "t", "u"))
    responses.get(BASE + "/functionalities", json=[])
    responses.post(BASE + "/documents", json={"uuid": "d1", "name": "a.pdf", "size": 3})
    responses.post(BASE + "/shares", json=[{"uuid": "s1", "name": "a.pdf", "recipient": {"mail": "x@y.org"}}])
    result = CliRunner().invoke(cli, ["--json", "share", str(two_files[0]), "--to", "x@y.org"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["shared"][0]["uuid"] == "s1"
