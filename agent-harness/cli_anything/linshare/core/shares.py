"""Sending documents: plan (dry-run), execute with rollback, list and revoke sent shares."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from cli_anything.linshare.core.documents import check_readable_file, delete_each, upload_document
from cli_anything.linshare.core.errors import LinShareError, ServerError, UsageError
from cli_anything.linshare.core.util import (
    end_of_day_ms,
    iso_from_ms,
    is_external,
    normalize_emails,
    parse_expires,
)
from cli_anything.linshare.utils.linshare_backend import LinShareClient

EXTERNAL_NOTE = "external = different email domain than yours (heuristic)"


def plan_share(
    files: list[Path],
    to: list[str],
    expires: str | None,
    subject: str | None,
    message: str | None,
    password_link: bool,
    own_email: str,
    today: date,
    alert_if_not_downloaded: str | None = None,
    send_me_a_copy: bool = False,
) -> dict:
    """Validate everything locally and describe exactly what would be sent. No HTTP."""
    checked = [check_readable_file(Path(f)) for f in files]
    emails = normalize_emails(to)
    expiry = parse_expires(expires, today) if expires else None
    alert = _parse_alert(alert_if_not_downloaded, today, expiry)
    return {
        "files": [{"path": str(f), "name": f.name, "size": f.stat().st_size} for f in checked],
        "recipients": [{"email": e, "external": is_external(e, own_email)} for e in emails],
        "expirationDate": expiry.isoformat() if expiry else None,
        "subject": subject,
        "message": message,
        "passwordLink": password_link,
        "alertIfNotDownloaded": alert.isoformat() if isinstance(alert, date) else alert,
        "sendMeACopy": bool(send_me_a_copy),
        "note": EXTERNAL_NOTE,
    }


def _parse_alert(value: str | None, today: date, expiry: date | None) -> date | bool | None:
    """None: leave it to the server; False: no alert; a date: alert on that day."""
    if value is None:
        return None
    if value.strip().lower() == "off":
        return False
    try:
        day = parse_expires(value, today)
    except UsageError:
        raise UsageError(
            f"Invalid alert date '{value}': use a future date (YYYY-MM-DD), a delay (3d, 1w) or 'off'"
        ) from None
    if expiry and day > expiry:
        raise UsageError(f"The alert date {day} is after the share expires ({expiry})")
    return day


ALERT = "UNDOWNLOADED_SHARED_DOCUMENTS_ALERT"
ALERT_DAYS = "UNDOWNLOADED_SHARED_DOCUMENTS_ALERT__DURATION"
COPY = "SHARE_CREATION_ACKNOWLEDGEMENT_FOR_OWNER"


def _after_working_days(today: date, days: int) -> date:
    """The server's own count (ShareServiceImpl): skips one weekend per 5 working days."""
    java_day_of_week = today.isoweekday() % 7 + 1  # Sunday = 1 … Saturday = 7
    weeks = int((java_day_of_week - 2 + days) / 5)
    return today + timedelta(days=days + weeks * 2)


def server_effects(client: LinShareClient, plan: dict, today: date) -> dict:
    """What the server will really do about the download alert and the copy to the sender,
    given the administrator's settings: a user's choice only counts where it may be overridden."""
    funcs = {f.get("identifier"): f for f in client.get("/functionalities") or []}
    return {
        "downloadAlert": _alert_effect(funcs, plan["alertIfNotDownloaded"], today),
        "copyToMe": _copy_effect(funcs.get(COPY), plan["sendMeACopy"]),
    }


def _alert_effect(funcs: dict, asked, today: date) -> dict:
    func = funcs.get(ALERT)
    if not func or not func.get("enable"):
        return {"enabled": False, "date": None, "detail": "not available on this server"}
    overridable = bool(func.get("canOverride"))
    enabled = bool(asked) if (overridable and asked is not None) else bool(func.get("value"))
    if not enabled:
        detail = "as you asked" if asked is False else "off by default on this server"
        return {"enabled": False, "date": None, "detail": detail}
    if asked is False:
        refused = "the administrator turns it on for every share"
    else:
        refused = None
    days = funcs.get(ALERT_DAYS) or {}
    if isinstance(asked, str) and days.get("canOverride"):
        return {"enabled": True, "date": asked, "detail": "as you asked"}
    default = _after_working_days(today, int(days.get("value") or 0)).isoformat()
    detail = f"server default: after {days.get('value')} working days"
    if isinstance(asked, str):
        detail += "; the administrator does not let you choose the date"
    if refused:
        detail = refused + "; " + detail
    return {"enabled": True, "date": default, "detail": detail}


def _copy_effect(func: dict | None, asked: bool) -> bool:
    if not func or not func.get("enable"):
        return False
    return asked if func.get("canOverride") else bool(func.get("value"))


def _share_body(plan: dict, document_uuids: list[str]) -> dict:
    body: dict = {
        "documents": document_uuids,
        "recipients": [{"mail": r["email"]} for r in plan["recipients"]],
    }
    if plan["expirationDate"]:
        body["expirationDate"] = end_of_day_ms(date.fromisoformat(plan["expirationDate"]))
    if plan["subject"]:
        body["subject"] = plan["subject"]
    if plan["message"]:
        body["message"] = plan["message"]
    body["secured"] = bool(plan["passwordLink"])
    alert = plan.get("alertIfNotDownloaded")
    if alert is not None:
        body["enableUSDA"] = bool(alert)
        if alert:
            body["notificationDateForUSDA"] = end_of_day_ms(date.fromisoformat(alert))
    body["creationAcknowledgement"] = bool(plan.get("sendMeACopy"))
    return body


def _project_share(s: dict) -> dict:
    return {
        "uuid": s.get("uuid"),
        "documentName": s.get("name"),
        "recipient": (s.get("recipient") or {}).get("mail"),
        "expirationDate": iso_from_ms(s.get("expirationDate")),
    }


def execute_share(client: LinShareClient, plan: dict) -> dict:
    """Upload the plan's files then share them. A failure removes what was uploaded,
    except when the server did not answer in time: then the outcome is unknown and is reported as such."""
    uploaded: list[str] = []
    current = None
    try:
        for f in plan["files"]:
            current = f["name"]
            uploaded.append(upload_document(client, Path(f["path"]))["uuid"])
        current = None
        created = client.post_json("/shares", _share_body(plan, uploaded)) or []
    except Exception as exc:  # noqa: BLE001 — any failure must go through the cleanup decision
        err = exc if isinstance(exc, LinShareError) else ServerError(f"{exc.__class__.__name__}: {exc}")
        timed_out = err.code == "timeout"
        if timed_out and current is None:
            err.message += (
                ". The share may have been sent: check `linshare shares list` before trying again."
                " Uploaded files were kept."
            )
            raise err from None
        if uploaded:
            try:
                delete_each(client, "/documents/{uuid}", uploaded, "document")
                err.message += " (uploaded files were removed)"
            except LinShareError:
                err.message += " (could not remove the uploaded documents: check `linshare documents list`)"
        if timed_out:
            err.message += f". '{current}' may have been stored anyway: check `linshare documents list`."
        raise err from None
    return {"shared": [_project_share(s) for s in created], "plan": plan}


def list_shares(client: LinShareClient) -> list[dict]:
    return [
        {
            "uuid": s.get("uuid"),
            "documentName": s.get("name"),
            "recipient": (s.get("recipient") or {}).get("mail"),
            "creationDate": iso_from_ms(s.get("creationDate")),
            "expirationDate": iso_from_ms(s.get("expirationDate")),
            "downloaded": s.get("downloaded"),
        }
        for s in client.get("/shares") or []
    ]


def revoke_shares(client: LinShareClient, uuids: list[str]) -> list[dict]:
    return delete_each(client, "/shares/{uuid}", uuids, "share")
