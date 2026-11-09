"""Regression tests for the final-review findings (I-1..I-5, expiry timezone)."""

import json
import time
from datetime import date

import pytest
import requests
import responses
from click.testing import CliRunner

from cli_anything.linshare.core import auth
from cli_anything.linshare.core.config import Config, load_config, save_config
from cli_anything.linshare.core.errors import ServerError
from cli_anything.linshare.core.shares import execute_share, plan_share
from cli_anything.linshare.core.util import end_of_day_ms
from cli_anything.linshare.linshare_cli import cli
from cli_anything.linshare.utils.linshare_backend import API_PATH, LinShareClient

SERVER = "https://ls.example.com"
BASE = SERVER + API_PATH
TODAY = date(2026, 11, 6)


# I-1: usage errors exit 1 (not 2 = auth) and are JSON with --json
def test_missing_option_is_usage_exit_1_json(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    result = CliRunner().invoke(cli, ["--json", "share", str(f)])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "usage"


def test_unknown_command_is_usage_exit_1():
    result = CliRunner().invoke(cli, ["--json", "shar"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "usage"


def test_help_still_exits_0():
    assert CliRunner().invoke(cli, ["share", "--help"]).exit_code == 0


# I-2: timeouts mean "outcome unknown", never a false cleanup claim
@responses.activate
def test_share_timeout_does_not_delete_and_says_unknown(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    responses.post(BASE + "/documents", json={"uuid": "d1", "name": "a.pdf", "size": 1})
    responses.post(BASE + "/shares", body=requests.ReadTimeout("slow"))
    plan = plan_share([f], ["x@y.org"], None, None, None, False, "me@y.org", TODAY)
    with pytest.raises(ServerError) as exc:
        execute_share(LinShareClient(SERVER, token="t"), plan)
    assert exc.value.code == "timeout"
    assert "shares list" in exc.value.message
    assert "removed" not in exc.value.message
    assert not [c for c in responses.calls if c.request.method == "DELETE"]


@responses.activate
def test_upload_timeout_cleans_earlier_and_warns_about_last(tmp_path):
    a = tmp_path / "a.pdf"
    a.write_bytes(b"x")
    b = tmp_path / "b.pdf"
    b.write_bytes(b"y")
    responses.post(BASE + "/documents", json={"uuid": "d1", "name": "a.pdf", "size": 1})
    responses.post(BASE + "/documents", body=requests.ReadTimeout("slow"))
    responses.delete(BASE + "/documents/d1", json={})
    plan = plan_share([a, b], ["x@y.org"], None, None, None, False, "me@y.org", TODAY)
    with pytest.raises(ServerError) as exc:
        execute_share(LinShareClient(SERVER, token="t"), plan)
    assert exc.value.code == "timeout"
    assert "b.pdf" in exc.value.message and "documents list" in exc.value.message
    assert [c.request.url for c in responses.calls if c.request.method == "DELETE"] == [BASE + "/documents/d1"]


@responses.activate
def test_non_linshare_exception_still_rolls_back(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x")
    responses.post(BASE + "/documents", json={"uuid": "d1", "name": "a.pdf", "size": 1})
    responses.post(BASE + "/shares", body=requests.exceptions.ChunkedEncodingError("broken"))
    responses.delete(BASE + "/documents/d1", json={})
    plan = plan_share([f], ["x@y.org"], None, None, None, False, "me@y.org", TODAY)
    with pytest.raises(ServerError) as exc:
        execute_share(LinShareClient(SERVER, token="t"), plan)
    assert "uploaded files were removed" in exc.value.message


def test_upload_uses_longer_read_timeout():
    c = LinShareClient(SERVER, token="t")
    assert c.upload_timeout[1] >= 600


# I-3: logout always forgets the local token
@responses.activate
def test_logout_with_rejected_token_still_clears():
    save_config(Config(SERVER, "me@y.org", "t", "u1"))
    responses.delete(BASE + "/jwt/u1", status=401)
    result = auth.logout()
    assert result["revokedOnServer"] is False
    assert "web UI" in result["note"]
    assert load_config().token is None


@responses.activate
def test_logout_with_server_down_still_clears():
    save_config(Config(SERVER, "me@y.org", "t", "u1"))
    responses.delete(BASE + "/jwt/u1", body=requests.ConnectionError("down"))
    assert auth.logout()["revokedOnServer"] is False
    assert load_config().token is None


# I-4: a failed download leaves no partial file
@responses.activate
def test_failed_download_leaves_no_file(tmp_path):
    responses.get(BASE + "/x/download", body=requests.exceptions.ChunkedEncodingError("cut"))
    target = tmp_path / "contract.pdf"
    with pytest.raises(ServerError):
        LinShareClient(SERVER, token="t").download("/x/download", target)
    assert list(tmp_path.iterdir()) == []


# Re-graded minor: expiry is the end of the day in the user's local timezone
def test_end_of_day_uses_local_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    # 2026-11-13 23:59:59 CET (UTC+1) == 22:59:59 UTC
    assert end_of_day_ms(date(2026, 11, 13)) == 1794610799000


@responses.activate
def test_download_never_deletes_a_preexisting_part_file(tmp_path):
    existing = tmp_path / "contract.pdf.part"
    existing.write_bytes(b"user data")
    responses.get(BASE + "/x/download", body=b"new")
    target = tmp_path / "contract.pdf"
    LinShareClient(SERVER, token="t").download("/x/download", target)
    assert existing.read_bytes() == b"user data"
    assert target.read_bytes() == b"new"


@responses.activate
def test_download_does_not_overwrite_file_created_meanwhile(tmp_path, monkeypatch):
    import cli_anything.linshare.utils.linshare_backend as backend

    target = tmp_path / "contract.pdf"
    responses.get(BASE + "/x/download", body=b"new")
    real_link = backend.os.link

    def racing_link(src, dst):
        target.write_bytes(b"appeared")  # another process creates the target first
        return real_link(src, dst)

    monkeypatch.setattr(backend.os, "link", racing_link)
    with pytest.raises(ServerError):
        LinShareClient(SERVER, token="t").download("/x/download", target)
    assert target.read_bytes() == b"appeared"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["contract.pdf"]
