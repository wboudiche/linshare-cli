"""LinShare user REST API (v5) backend — the only module that makes network requests."""

from __future__ import annotations

import math
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from cli_anything.linshare.core.errors import (
    AuthError,
    BusinessError,
    ServerError,
    plain_message,
)

API_PATH = "/linshare/webservice/rest/user/v5"
TOTP_ERROR_CODES = {"1002", "1003"}

MiB = 1024 * 1024
FLOW_MIN_CHUNK = 2 * MiB
FLOW_MAX_CHUNK = 100 * MiB
FLOW_RETRIES = 3  # per chunk, one second apart, like the web UI


def flow_chunk_size(total_size: int) -> int:
    """Chunk size used by the LinShare web UI: 2 % of the file, in whole MiB, between 2 and 100 MiB."""
    proposed = math.ceil(total_size * 0.02 / MiB) * MiB
    return min(max(proposed, FLOW_MIN_CHUNK), FLOW_MAX_CHUNK)


class LinShareClient:
    def __init__(
        self,
        server: str,
        token: str | None = None,
        basic: tuple[str, str] | None = None,
        totp: str | None = None,
        timeout: float = 60,
        upload_timeout: float = 900,
    ):
        self.server = server.rstrip("/")
        self.base_url = self.server + API_PATH
        # (connect, read) seconds; uploads get a long read timeout (server-side checksum, antivirus, encryption)
        self.timeout = (10, timeout)
        self.upload_timeout = (10, upload_timeout)
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
            self.session.headers["X-LinShare-Auth-Provider"] = "Jwt"
        if basic:
            self.session.auth = basic
        if totp:
            self.session.headers["X-LinShare-2fa-pin"] = totp

    # -- public verbs -------------------------------------------------------

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._json(self._send("GET", path, params=params))

    def post_json(self, path: str, body: Any, params: dict | None = None) -> Any:
        return self._json(self._send("POST", path, params=params, json=body))

    def put(self, path: str, body: Any = None) -> Any:
        return self._json(self._send("PUT", path, json=body))

    def delete(self, path: str) -> Any:
        return self._json(self._send("DELETE", path))

    def upload(self, path: str, file_path: Path, extra_fields: dict) -> dict:
        size = file_path.stat().st_size
        # No separate "filename" field: LinShare mis-decodes non-ASCII text fields; the file part carries the name.
        data = {"filesize": str(size), **extra_fields}
        with file_path.open("rb") as fh:
            resp = self._send(
                "POST", path, data=data, files={"file": (file_path.name, fh)}, timeout=self.upload_timeout
            )
        return self._json(resp)

    def upload_flow(self, file_path: Path, extra_fields: dict | None = None, require_entry: bool = True) -> dict:
        """Send a file in chunks (POST /flow), one chunk in memory at a time; returns the document.

        A chunk that fails on the network or with a 5xx is sent again (the server ignores chunks it
        already has). The last chunk never is: the server may have created the document already.
        `extra_fields` go with every chunk (workGroupUuid…). Into a workgroup the server answers
        without the new node's uuid: then pass require_entry=False and find it afterwards."""
        total = file_path.stat().st_size
        chunk_size = flow_chunk_size(total)
        count = max(1, math.ceil(total / chunk_size))  # an empty file is one empty chunk
        identifier = f"{total}-{uuid.uuid4().hex}"
        with file_path.open("rb") as fh:
            for number in range(1, count + 1):
                chunk = fh.read(chunk_size)
                fields = {
                    "flowChunkNumber": number,
                    "flowTotalChunks": count,
                    "flowChunkSize": chunk_size,
                    "flowCurrentChunkSize": len(chunk),
                    "flowTotalSize": total,
                    "flowIdentifier": identifier,
                    "flowFilename": file_path.name,
                    "flowRelativePath": file_path.name,
                    **(extra_fields or {}),
                }
                files = {k: (None, str(v)) for k, v in fields.items()}
                files["file"] = (file_path.name, chunk, "application/octet-stream")
                last = number == count
                result = self._send_chunk(files, retries=0 if last else FLOW_RETRIES)
                if not result.get("chunkUploadSuccess"):
                    code = result.get("errCode")
                    message = result.get("errorMessage") or "LinShare refused part of the file"
                    raise BusinessError(
                        plain_message(code, message) if code else message, linshare_error_code=code
                    )
        if not result.get("lastChunk") or (require_entry and not (result.get("entry") or {}).get("uuid")):
            raise ServerError(f"LinShare did not finish storing '{file_path.name}': nothing was created")
        return result.get("entry") or {}

    def _send_chunk(self, files: dict, retries: int) -> dict:
        for attempt in range(retries + 1):
            try:
                resp = self._send("POST", "/flow", files=files, timeout=self.upload_timeout)
                return self._json(resp) or {}
            except ServerError:
                if attempt == retries:
                    raise
                time.sleep(1)
        raise AssertionError("unreachable")

    def download(self, path: str, target: Path) -> Path:
        resp = self._send("GET", path, stream=True, headers={"Accept": "*/*"})
        # Write to a temp file we own, then hard-link it to the target: link() fails if the target exists,
        # so nothing is ever overwritten, and cleanup only ever touches our own temp file.
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".part")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    fh.write(chunk)
            try:
                os.link(tmp, target)
            except FileExistsError:
                raise ServerError(f"'{target}' appeared during the download; kept the existing file") from None
        except requests.RequestException as exc:
            raise ServerError(f"Download interrupted ({exc.__class__.__name__}); nothing was saved") from None
        finally:
            resp.close()
            tmp.unlink(missing_ok=True)
        return target

    # -- internals ----------------------------------------------------------

    def _send(self, method: str, path: str, timeout=None, **kwargs) -> requests.Response:
        try:
            resp = self.session.request(method, self.base_url + path, timeout=timeout or self.timeout, **kwargs)
        except requests.Timeout:
            raise ServerError(
                f"No answer from LinShare server at {self.server} in time: the operation may or may not have been done",
                code="timeout",
            ) from None
        except requests.ConnectionError as exc:
            raise ServerError(f"Cannot reach LinShare server at {self.server} ({exc.__class__.__name__})") from None
        except requests.RequestException as exc:
            raise ServerError(f"Request to LinShare failed ({exc.__class__.__name__}: {exc})") from None
        if resp.status_code >= 400:
            self._raise_for(resp)
        return resp

    @staticmethod
    def _json(resp: requests.Response) -> Any:
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def _raise_for(self, resp: requests.Response) -> None:
        status = resp.status_code
        auth_code = resp.headers.get("X-LinShare-Auth-error-code")
        if status == 401:
            if auth_code in TOTP_ERROR_CODES:
                raise AuthError(
                    "This account uses two-factor authentication: a valid 2FA code is required",
                    code="totp_required",
                    http_status=status,
                )
            if self.session.auth:
                raise AuthError("Wrong email or password for this LinShare server", http_status=status)
            raise AuthError(
                "Not logged in or token rejected — ask the user to run `linshare login` in their terminal",
                http_status=status,
            )
        body: dict = {}
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            pass
        err_code = body.get("errCode")
        server_msg = body.get("message") or resp.reason or f"HTTP {status}"
        if status >= 500:
            raise ServerError(
                f"LinShare server error (HTTP {status}): {server_msg}",
                http_status=status,
                linshare_error_code=err_code,
            )
        message = plain_message(err_code, server_msg) if err_code is not None else server_msg
        raise BusinessError(message, http_status=status, linshare_error_code=err_code)
