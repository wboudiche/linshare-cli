"""login (password + optional 2FA, or pasted token), logout, whoami."""

from __future__ import annotations

import re
import socket
from datetime import datetime

from cli_anything.linshare.core.config import (
    Config,
    clear_config,
    load_config,
    require_login,
    save_config,
)
from cli_anything.linshare.core.errors import AuthError, LinShareError
from cli_anything.linshare.utils.linshare_backend import LinShareClient

MIN_VERSION = (6, 5)
_VERSION = re.compile(r"^(\d+)\.(\d+)")


def check_version(client: LinShareClient) -> str:
    version = str((client.get("/authentication/version") or {}).get("version", ""))
    match = _VERSION.match(version)
    if not match or (int(match.group(1)), int(match.group(2))) < MIN_VERSION:
        raise AuthError(f"LinShare 6.5 or later is required (server is {version or 'unknown'})")
    return version


def login_password(server: str, email: str, password: str, totp: str | None) -> Config:
    client = LinShareClient(server, basic=(email, password), totp=totp)
    check_version(client)
    host = socket.gethostname()
    created = client.post_json(
        "/jwt",
        {
            "label": f"linshare-cli@{host}",
            "description": f"Created by linshare CLI on {datetime.now().isoformat(timespec='seconds')}",
        },
    )
    cfg = Config(server.rstrip("/"), email, created["token"], created.get("uuid"))
    save_config(cfg)
    return cfg


def login_token(server: str, token: str) -> Config:
    client = LinShareClient(server, token=token)
    check_version(client)
    me = client.get("/authentication/authorized") or {}
    cfg = Config(server.rstrip("/"), me.get("mail"), token, None)
    save_config(cfg)
    return cfg


def logout() -> dict:
    cfg = load_config()
    revoked = False
    note = None
    if cfg.server and cfg.token and cfg.token_uuid:
        try:
            LinShareClient(cfg.server, token=cfg.token).delete(f"/jwt/{cfg.token_uuid}")
            revoked = True
        except LinShareError as err:
            note = (
                f"Token removed locally but not revoked on the server ({err.message}). "
                "Revoke it in the LinShare web UI if it is still valid."
            )
    elif cfg.token:
        note = "Token removed locally. Revoke it in the LinShare web UI (Account → API tokens)."
    clear_config()
    return {"revokedOnServer": revoked, "note": note}


def client_from_config() -> tuple[LinShareClient, Config]:
    cfg = require_login(load_config())
    return LinShareClient(cfg.server, token=cfg.token), cfg


def whoami() -> dict:
    client, cfg = client_from_config()
    me = client.get("/authentication/authorized") or {}
    return {
        "server": cfg.server,
        "email": me.get("mail", cfg.email),
        "firstName": me.get("firstName"),
        "lastName": me.get("lastName"),
        "serverVersion": (client.get("/authentication/version") or {}).get("version"),
    }
