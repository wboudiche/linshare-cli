"""The user's own documents: upload, list, delete."""

from __future__ import annotations

from pathlib import Path

from cli_anything.linshare.core.errors import LinShareError, UsageError
from cli_anything.linshare.core.util import iso_from_ms
from cli_anything.linshare.utils.linshare_backend import LinShareClient, flow_chunk_size


def check_readable_file(path: Path) -> Path:
    if not path.is_file():
        raise UsageError(f"Not a file: '{path}'")
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise UsageError(f"Cannot read '{path}': {exc.strerror}") from None
    return path


def upload_document(client: LinShareClient, path: Path) -> dict:
    check_readable_file(path)
    size = path.stat().st_size
    if size > flow_chunk_size(size):
        doc = client.upload_flow(path)
    else:
        doc = client.upload("/documents", path, {"description": ""})
    return {"uuid": doc.get("uuid"), "name": doc.get("name"), "size": doc.get("size")}


def list_documents(client: LinShareClient) -> list[dict]:
    return [
        {
            "uuid": d.get("uuid"),
            "name": d.get("name"),
            "size": d.get("size"),
            "creationDate": iso_from_ms(d.get("creationDate")),
            "expirationDate": iso_from_ms(d.get("expirationDate")),
        }
        for d in client.get("/documents") or []
    ]


def delete_each(client: LinShareClient, path_template: str, uuids: list[str], what: str) -> list[dict]:
    """DELETE each uuid in order; on failure say which ones were already done."""
    done: list[dict] = []
    for uuid in uuids:
        try:
            client.delete(path_template.format(uuid=uuid))
        except LinShareError as err:
            already = ", ".join(d["uuid"] for d in done) or "none"
            err.message = f"Could not delete {what} {uuid}: {err.message} (already deleted: {already})"
            raise
        done.append({"uuid": uuid, "deleted": True})
    return done


def delete_documents(client: LinShareClient, uuids: list[str]) -> list[dict]:
    return delete_each(client, "/documents/{uuid}", uuids, "document")
