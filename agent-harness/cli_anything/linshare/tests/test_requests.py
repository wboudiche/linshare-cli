import json
from datetime import date

import pytest
import responses
from click.testing import CliRunner

from cli_anything.linshare.core.config import Config, save_config
from cli_anything.linshare.core.upload_requests import (
    close_requests,
    create_request,
    download_request,
    list_requests,
    plan_request,
)
from cli_anything.linshare.linshare_cli import cli
from cli_anything.linshare.utils.linshare_backend import API_PATH, LinShareClient

SERVER = "https://ls.example.com"
BASE = SERVER + API_PATH
TODAY = date(2026, 11, 6)
END_OF_13_NOV_MS = 1794614399000


def client():
    return LinShareClient(SERVER, token="t")


def test_plan_request_defaults():
    plan = plan_request(["Ann@Acme.com", "ann@acme.com"], None, "7d", "Send me the scans", "me@linagora.com", TODAY)
    assert plan == {
        "recipients": [{"email": "ann@acme.com", "external": True}],
        "label": "Files for me@linagora.com",
        "expiryDate": "2026-11-13",
        "message": "Send me the scans",
        "collective": False,
        "note": "external = different email domain than yours (heuristic)",
    }


@responses.activate
def test_create_request_is_never_collective():
    responses.post(BASE + "/upload_request_groups", json={"uuid": "g1", "label": "L"})
    plan = plan_request(["a@x.org", "b@y.org"], "L", "2026-11-13", "hi", "me@linagora.com", TODAY)
    assert create_request(client(), plan) == {"uuid": "g1", "label": "L", "recipients": ["a@x.org", "b@y.org"]}
    req = responses.calls[0].request
    assert req.url.endswith("?collective=false")
    assert json.loads(req.body) == {
        "label": "L",
        "contactList": ["a@x.org", "b@y.org"],
        "expiryDate": END_OF_13_NOV_MS,
        "body": "hi",
        "enableNotification": True,
    }


@responses.activate
def test_request_dry_run_no_http():
    save_config(Config(SERVER, "me@linagora.com", "t", "u"))
    result = CliRunner().invoke(cli, ["--json", "request-files", "--from", "a@x.org", "--message", "hi", "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dryRun"] is True and payload["collective"] is False
    assert len(responses.calls) == 0


@responses.activate
def test_list_requests_projection():
    responses.get(
        BASE + "/upload_request_groups",
        json=[
            {
                "uuid": "g1",
                "label": "L",
                "status": "ENABLED",
                "expiryDate": END_OF_13_NOV_MS,
                "nbrUploadedFiles": 2,
                "body": "x",
            }
        ],
    )
    assert list_requests(client()) == [
        {"uuid": "g1", "label": "L", "status": "ENABLED", "expiryDate": "2026-11-13T23:59:59Z", "uploadedFiles": 2}
    ]


@responses.activate
def test_download_request_zip_named_after_label(tmp_path):
    responses.get(BASE + "/upload_request_groups/g1", json={"uuid": "g1", "label": "Scans 2026"})
    responses.get(BASE + "/upload_request_groups/g1/download", body=b"PK")
    assert download_request(client(), "g1", tmp_path) == {"uuid": "g1", "path": str(tmp_path / "Scans 2026.zip")}


@responses.activate
def test_close_uses_closed_status():
    responses.put(BASE + "/upload_request_groups/g1/status/CLOSED", json={"uuid": "g1", "status": "CLOSED"})
    assert close_requests(client(), ["g1"]) == [{"uuid": "g1", "status": "CLOSED"}]


@pytest.mark.parametrize("message", [None, "", "   "])
def test_plan_request_requires_a_message(message):
    # both 6.5 and 7.0 refuse an upload request without a body
    from cli_anything.linshare.core.errors import UsageError

    with pytest.raises(UsageError, match="--message"):
        plan_request(["a@x.org"], None, "7d", message, "me@linagora.com", TODAY)


@responses.activate
def test_request_without_message_is_a_usage_error_even_in_dry_run():
    save_config(Config(SERVER, "me@linagora.com", "t", "u"))
    result = CliRunner().invoke(cli, ["--json", "request-files", "--from", "a@x.org", "--dry-run"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["error"]["code"] == "usage"
    assert len(responses.calls) == 0
