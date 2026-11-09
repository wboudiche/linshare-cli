import json

import pytest
import responses

from cli_anything.linshare.core import auth
from cli_anything.linshare.core.config import Config, load_config, save_config
from cli_anything.linshare.core.errors import AuthError
from cli_anything.linshare.utils.linshare_backend import API_PATH

SERVER = "https://ls.example.com"
BASE = SERVER + API_PATH
ME = {"mail": "me@example.com", "firstName": "Jane", "lastName": "Doe"}


@responses.activate
def test_login_password_creates_token_and_saves(isolated_config):
    responses.get(BASE + "/authentication/version", json={"version": "6.5.4"})
    responses.post(BASE + "/jwt", json={"uuid": "u1", "token": "t1"})
    cfg = auth.login_password(SERVER, "me@example.com", "s3cret-pw", None)
    assert cfg == Config(SERVER, "me@example.com", "t1", "u1")
    assert load_config() == cfg
    assert "s3cret-pw" not in (isolated_config / "config.json").read_text()
    body = json.loads(responses.calls[1].request.body)
    assert body["label"].startswith("linshare-cli@")
    assert "Created by linshare CLI" in body["description"]


@pytest.mark.parametrize("version", ["6.4.2", "5.1.0", "garbage"])
@responses.activate
def test_login_rejects_old_server(version):
    responses.get(BASE + "/authentication/version", json={"version": version})
    with pytest.raises(AuthError) as exc:
        auth.login_password(SERVER, "me@example.com", "pw", None)
    assert "6.5 or later" in exc.value.message
    assert load_config().token is None


@pytest.mark.parametrize("version", ["6.5.0", "7.0.0-SNAPSHOT", "10.1.2"])
@responses.activate
def test_check_version_accepts_supported(version):
    from cli_anything.linshare.utils.linshare_backend import LinShareClient

    responses.get(BASE + "/authentication/version", json={"version": version})
    assert auth.check_version(LinShareClient(SERVER, token="t")) == version


@responses.activate
def test_login_token_mode_saves_without_uuid():
    responses.get(BASE + "/authentication/version", json={"version": "7.0.0"})
    responses.get(BASE + "/authentication/authorized", json=ME)
    cfg = auth.login_token(SERVER, "pasted")
    assert cfg == Config(SERVER, "me@example.com", "pasted", None)
    assert responses.calls[1].request.headers["Authorization"] == "Bearer pasted"


@responses.activate
def test_logout_revokes_on_server_when_uuid():
    save_config(Config(SERVER, "me@example.com", "t1", "u1"))
    responses.delete(BASE + "/jwt/u1", json={"uuid": "u1"})
    assert auth.logout() == {"revokedOnServer": True, "note": None}
    assert load_config().token is None


def test_logout_local_only_note_when_no_uuid():
    save_config(Config(SERVER, "me@example.com", "t1", None))
    result = auth.logout()
    assert result["revokedOnServer"] is False
    assert "web UI" in result["note"]
    assert load_config().token is None


@responses.activate
def test_whoami():
    save_config(Config(SERVER, "me@example.com", "t1", "u1"))
    responses.get(BASE + "/authentication/authorized", json=ME)
    responses.get(BASE + "/authentication/version", json={"version": "6.5.4"})
    assert auth.whoami() == {
        "server": SERVER,
        "email": "me@example.com",
        "firstName": "Jane",
        "lastName": "Doe",
        "serverVersion": "6.5.4",
    }
