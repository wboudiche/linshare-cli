"""Alert when a share is not downloaded, and copy of the share email to the sender.

The server applies the administrator's policies (functionalities): a user's choice only
counts where the admin lets users override it, so the dry run reads them and says what
will really happen."""

import json
from datetime import date

import pytest
import responses
from click.testing import CliRunner

from cli_anything.linshare.core.config import Config, save_config
from cli_anything.linshare.core.errors import UsageError
from cli_anything.linshare.core.shares import _share_body, plan_share, server_effects
from cli_anything.linshare.linshare_cli import cli
from cli_anything.linshare.utils.linshare_backend import API_PATH, LinShareClient

SERVER = "https://ls.example.com"
BASE = SERVER + API_PATH
TODAY = date(2026, 11, 6)  # a Friday


@pytest.fixture
def doc(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"aaa")
    return f


def plan(doc, alert=None, copy=False, expires=None):
    return plan_share([doc], ["x@y.org"], expires, None, None, False, "me@y.org", TODAY,
                      alert_if_not_downloaded=alert, send_me_a_copy=copy)


def funcs(alert=True, alert_override=True, days=3, days_override=True, ack=True, ack_override=True, alert_enabled=True):
    return [
        {"identifier": "UNDOWNLOADED_SHARED_DOCUMENTS_ALERT", "type": "boolean",
         "enable": alert_enabled, "canOverride": alert_override, "value": alert},
        {"identifier": "UNDOWNLOADED_SHARED_DOCUMENTS_ALERT__DURATION", "type": "integer",
         "enable": True, "canOverride": days_override, "value": days},
        {"identifier": "SHARE_CREATION_ACKNOWLEDGEMENT_FOR_OWNER", "type": "boolean",
         "enable": True, "canOverride": ack_override, "value": ack},
        {"identifier": "SHARE_EXPIRATION", "type": "unit", "enable": True, "canOverride": True},
    ]


def effects(p, **kw):
    responses.get(BASE + "/functionalities", json=funcs(**kw))
    return server_effects(LinShareClient(SERVER, token="t"), p, TODAY)


# -- plan ---------------------------------------------------------------------


def test_plan_keeps_the_requested_alert_date(doc):
    assert plan(doc, "3d")["alertIfNotDownloaded"] == "2026-11-09"
    assert plan(doc, "2026-11-20")["alertIfNotDownloaded"] == "2026-11-20"


def test_plan_alert_off_and_not_asked(doc):
    assert plan(doc, "off")["alertIfNotDownloaded"] is False
    assert plan(doc)["alertIfNotDownloaded"] is None
    assert plan(doc)["sendMeACopy"] is False
    assert plan(doc, copy=True)["sendMeACopy"] is True


@pytest.mark.parametrize("value", ["yesterday", "2026-11-01", "0d"])
def test_plan_rejects_a_bad_alert_date(doc, value):
    with pytest.raises(UsageError):
        plan(doc, value)


def test_plan_rejects_an_alert_after_expiry(doc):
    with pytest.raises(UsageError, match="after the share expires"):
        plan(doc, "2026-11-20", expires="2026-11-13")


# -- request body ---------------------------------------------------------------


def test_body_with_alert_date_and_copy(doc):
    body = _share_body(plan(doc, "2026-11-13", copy=True), ["d1"])
    assert body["enableUSDA"] is True
    assert body["notificationDateForUSDA"] == 1794614399000  # 2026-11-13T23:59:59Z
    assert body["creationAcknowledgement"] is True


def test_body_alert_off(doc):
    body = _share_body(plan(doc, "off"), ["d1"])
    assert body["enableUSDA"] is False
    assert "notificationDateForUSDA" not in body


def test_body_leaves_the_alert_to_the_server_when_not_asked(doc):
    body = _share_body(plan(doc), ["d1"])
    assert "enableUSDA" not in body and "notificationDateForUSDA" not in body
    assert body["creationAcknowledgement"] is False


# -- what the server will really do ---------------------------------------------------


@responses.activate
def test_server_default_alert_after_working_days(doc):
    # Friday + 3 working days = next Wednesday, as the server counts
    out = effects(plan(doc))
    assert out["downloadAlert"]["enabled"] is True
    assert out["downloadAlert"]["date"] == "2026-11-11"
    assert "server default" in out["downloadAlert"]["detail"]


@responses.activate
def test_requested_alert_date_is_used_when_allowed(doc):
    out = effects(plan(doc, "2026-11-20"))
    assert out["downloadAlert"] == {"enabled": True, "date": "2026-11-20", "detail": "as you asked"}


@responses.activate
def test_requested_date_not_allowed_falls_back_to_the_server_date(doc):
    out = effects(plan(doc, "2026-11-20"), days_override=False)
    assert out["downloadAlert"]["date"] == "2026-11-11"
    assert "does not let you choose the date" in out["downloadAlert"]["detail"]


@responses.activate
def test_alert_turned_off_by_the_admin(doc):
    out = effects(plan(doc, "2026-11-20"), alert_enabled=False)
    assert out["downloadAlert"]["enabled"] is False
    assert out["downloadAlert"]["date"] is None
    assert "not available" in out["downloadAlert"]["detail"]


@responses.activate
def test_alert_off_refused_when_admin_forces_it(doc):
    out = effects(plan(doc, "off"), alert_override=False)
    assert out["downloadAlert"]["enabled"] is True
    assert "administrator" in out["downloadAlert"]["detail"]


@responses.activate
def test_alert_off_when_allowed(doc):
    out = effects(plan(doc, "off"))
    assert out["downloadAlert"] == {"enabled": False, "date": None, "detail": "as you asked"}


@responses.activate
def test_copy_to_me_follows_the_admin_when_not_overridable(doc):
    assert effects(plan(doc, copy=True))["copyToMe"] is True
    responses.reset()
    assert effects(plan(doc, copy=False))["copyToMe"] is False
    responses.reset()
    assert effects(plan(doc, copy=False), ack_override=False, ack=True)["copyToMe"] is True


# -- command line -------------------------------------------------------------------


@responses.activate
def test_dry_run_only_reads_the_server_settings(doc):
    save_config(Config(SERVER, "me@y.org", "t", "u"))
    responses.get(BASE + "/functionalities", json=funcs())
    result = CliRunner().invoke(cli, ["--json", "share", str(doc), "--to", "x@y.org",
                                      "--alert-if-not-downloaded", "2026-12-01", "--send-me-a-copy", "--dry-run"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["downloadAlert"]["date"] == "2026-12-01"
    assert out["copyToMe"] is True
    assert [c.request.method for c in responses.calls] == ["GET"]


@responses.activate
def test_real_run_reports_the_alert(doc):
    save_config(Config(SERVER, "me@y.org", "t", "u"))
    responses.get(BASE + "/functionalities", json=funcs())
    responses.post(BASE + "/documents", json={"uuid": "d1", "name": "a.pdf", "size": 3})
    responses.post(BASE + "/shares", json=[{"uuid": "s1", "name": "a.pdf", "recipient": {"mail": "x@y.org"}}])
    result = CliRunner().invoke(cli, ["--json", "share", str(doc), "--to", "x@y.org"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["downloadAlert"]["date"] is not None
    sent = json.loads(responses.calls[-1].request.body)
    assert "enableUSDA" not in sent
