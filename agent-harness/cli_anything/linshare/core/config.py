"""Login settings: server, email and permanent token, stored 0600. Never the password."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from cli_anything.linshare.core.errors import AuthError

CONFIG_FILE = "config.json"


@dataclass
class Config:
    server: str | None
    email: str | None
    token: str | None
    token_uuid: str | None


def config_dir() -> Path:
    override = os.environ.get("LINSHARE_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".cli-anything-linshare"


def _strip(server: str | None) -> str | None:
    return server.rstrip("/") if server else server


def load_config() -> Config:
    path = config_dir() / CONFIG_FILE
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return Config(
        server=_strip(os.environ.get("LINSHARE_SERVER") or data.get("server")),
        email=data.get("email"),
        token=os.environ.get("LINSHARE_TOKEN") or data.get("token"),
        token_uuid=data.get("tokenUuid"),
    )


def save_config(cfg: Config) -> None:
    directory = config_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / CONFIG_FILE
    payload = {
        "server": _strip(cfg.server),
        "email": cfg.email,
        "token": cfg.token,
        "tokenUuid": cfg.token_uuid,
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.chmod(path, 0o600)


def clear_config() -> None:
    path = config_dir() / CONFIG_FILE
    if path.exists():
        path.unlink()


def require_login(cfg: Config) -> Config:
    if not cfg.server or not cfg.token:
        raise AuthError("Not logged in — ask the user to run `linshare login` in their terminal")
    return cfg
