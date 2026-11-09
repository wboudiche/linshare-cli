"""Asking people to send files (LinShare upload requests). Always individual, never collective."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from cli_anything.linshare.core.errors import LinShareError, UsageError
from cli_anything.linshare.core.shares import EXTERNAL_NOTE
from cli_anything.linshare.core.util import (
    end_of_day_ms,
    iso_from_ms,
    is_external,
    normalize_emails,
    parse_expires,
    safe_target_path,
)
from cli_anything.linshare.utils.linshare_backend import LinShareClient


def plan_request(
    from_: list[str],
    label: str | None,
    expires: str | None,
    message: str | None,
    own_email: str,
    today: date,
) -> dict:
    """Validate locally and describe the request that would be created. No HTTP."""
    if not message or not message.strip():
        # LinShare 6.5 and 7.0 both refuse an upload request without a body
        raise UsageError("A message is required: add --message with the text the people asked will read.")
    emails = normalize_emails(from_)
    expiry = parse_expires(expires, today) if expires else None
    return {
        "recipients": [{"email": e, "external": is_external(e, own_email)} for e in emails],
        "label": label or f"Files for {own_email}",
        "expiryDate": expiry.isoformat() if expiry else None,
        "message": message,
        "collective": False,
        "note": EXTERNAL_NOTE,
    }


def create_request(client: LinShareClient, plan: dict) -> dict:
    body: dict = {
        "label": plan["label"],
        "contactList": [r["email"] for r in plan["recipients"]],
    }
    if plan["expiryDate"]:
        body["expiryDate"] = end_of_day_ms(date.fromisoformat(plan["expiryDate"]))
    body["body"] = plan["message"]
    body["enableNotification"] = True
    group = client.post_json("/upload_request_groups", body, params={"collective": "false"}) or {}
    return {"uuid": group.get("uuid"), "label": group.get("label"), "recipients": body["contactList"]}


def list_requests(client: LinShareClient) -> list[dict]:
    return [
        {
            "uuid": g.get("uuid"),
            "label": g.get("label"),
            "status": g.get("status"),
            "expiryDate": iso_from_ms(g.get("expiryDate")),
            "uploadedFiles": g.get("nbrUploadedFiles"),
        }
        for g in client.get("/upload_request_groups") or []
    ]


def download_request(client: LinShareClient, uuid: str, out_dir: Path) -> dict:
    if not out_dir.is_dir():
        raise UsageError(f"Not a directory: '{out_dir}'")
    label = (client.get(f"/upload_request_groups/{uuid}") or {}).get("label") or uuid
    target = client.download(f"/upload_request_groups/{uuid}/download", safe_target_path(out_dir, f"{label}.zip"))
    return {"uuid": uuid, "path": str(target)}


def close_requests(client: LinShareClient, uuids: list[str]) -> list[dict]:
    done: list[dict] = []
    for uuid in uuids:
        try:
            client.put(f"/upload_request_groups/{uuid}/status/CLOSED")
        except LinShareError as err:
            already = ", ".join(d["uuid"] for d in done) or "none"
            err.message = f"Could not close request {uuid}: {err.message} (already closed: {already})"
            raise
        done.append({"uuid": uuid, "status": "CLOSED"})
    return done
