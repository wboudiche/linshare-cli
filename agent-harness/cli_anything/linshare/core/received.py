"""Documents others shared with the user: list and download."""

from __future__ import annotations

from pathlib import Path

from cli_anything.linshare.core.errors import UsageError
from cli_anything.linshare.core.util import iso_from_ms, safe_target_path
from cli_anything.linshare.utils.linshare_backend import LinShareClient


def list_received(client: LinShareClient) -> list[dict]:
    return [
        {
            "uuid": r.get("uuid"),
            "name": r.get("name"),
            "size": r.get("size"),
            "sender": (r.get("sender") or {}).get("mail"),
            "creationDate": iso_from_ms(r.get("creationDate")),
            "expirationDate": iso_from_ms(r.get("expirationDate")),
        }
        for r in client.get("/received_shares") or []
    ]


def download_received(client: LinShareClient, uuid: str, out_dir: Path) -> dict:
    if not out_dir.is_dir():
        raise UsageError(f"Not a directory: '{out_dir}'")
    name = (client.get(f"/received_shares/{uuid}") or {}).get("name") or uuid
    target = client.download(f"/received_shares/{uuid}/download", safe_target_path(out_dir, name))
    return {"uuid": uuid, "path": str(target)}
