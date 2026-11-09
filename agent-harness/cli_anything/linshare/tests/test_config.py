import json
import stat

import pytest

from cli_anything.linshare.core.config import (
    Config,
    clear_config,
    config_dir,
    load_config,
    require_login,
    save_config,
)
from cli_anything.linshare.core.errors import AuthError


def _cfg(**kw):
    base = dict(server="https://ls.example.com", email="me@example.com", token="t1", token_uuid="u1")
    base.update(kw)
    return Config(**base)


def test_config_dir_uses_env(isolated_config):
    assert config_dir() == isolated_config


def test_save_then_load_roundtrip():
    save_config(_cfg())
    assert load_config() == _cfg()


def test_file_mode_is_0600(isolated_config):
    save_config(_cfg())
    path = isolated_config / "config.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(isolated_config.stat().st_mode) == 0o700
    assert json.loads(path.read_text()) == {
        "server": "https://ls.example.com",
        "email": "me@example.com",
        "token": "t1",
        "tokenUuid": "u1",
    }


def test_missing_file_gives_empty_config():
    assert load_config() == Config(None, None, None, None)


def test_env_overrides_file(monkeypatch):
    save_config(_cfg())
    monkeypatch.setenv("LINSHARE_SERVER", "https://other.example.com/")
    monkeypatch.setenv("LINSHARE_TOKEN", "envtoken")
    cfg = load_config()
    assert cfg.server == "https://other.example.com"
    assert cfg.token == "envtoken"
    assert cfg.email == "me@example.com"


def test_server_trailing_slash_stripped():
    save_config(_cfg(server="https://ls.example.com/"))
    assert load_config().server == "https://ls.example.com"


def test_clear_config():
    save_config(_cfg())
    clear_config()
    assert load_config().token is None
    clear_config()  # idempotent


def test_require_login_raises_auth_error():
    with pytest.raises(AuthError) as exc:
        require_login(load_config())
    assert "linshare login" in exc.value.message
    assert exc.value.exit_code == 2


def test_require_login_returns_config_when_logged_in():
    save_config(_cfg())
    assert require_login(load_config()).token == "t1"
