import json
import os
import subprocess
import sys

import responses
from click.testing import CliRunner

from cli_anything.linshare.core.config import load_config
from cli_anything.linshare.linshare_cli import cli
from cli_anything.linshare.utils.linshare_backend import API_PATH

SERVER = "https://ls.example.com"
BASE = SERVER + API_PATH


def test_cli_json_error_shape_and_exit_code():
    result = CliRunner().invoke(cli, ["--json", "whoami"])
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "auth"
    assert "linshare login" in payload["error"]["message"]


def test_cli_plain_error_goes_to_stderr():
    result = CliRunner().invoke(cli, ["whoami"])
    assert result.exit_code == 2
    assert "linshare login" in result.stderr


@responses.activate
def test_login_retries_with_totp():
    responses.get(
        BASE + "/authentication/version",
        status=401,
        headers={"X-LinShare-Auth-error-code": "1002"},
    )
    responses.get(BASE + "/authentication/version", json={"version": "6.5.4"})
    responses.post(BASE + "/jwt", json={"uuid": "u1", "token": "t1"})
    result = CliRunner().invoke(
        cli,
        ["login", "--server", SERVER, "--email", "me@example.com"],
        input="pw\n123456\n",
    )
    assert result.exit_code == 0, result.output
    assert responses.calls[1].request.headers["X-LinShare-2fa-pin"] == "123456"
    assert load_config().token == "t1"


@responses.activate
def test_login_token_option():
    responses.get(BASE + "/authentication/version", json={"version": "7.0.0"})
    responses.get(BASE + "/authentication/authorized", json={"mail": "me@example.com"})
    result = CliRunner().invoke(cli, ["--json", "login", "--server", SERVER, "--token"], input="pasted\n")
    assert result.exit_code == 0, result.output
    assert load_config().token == "pasted"
    assert "pasted" not in result.output


def _all_command_paths(group, prefix=()):
    import click

    for name, cmd in group.commands.items():
        if cmd.hidden:
            continue
        yield prefix + (name,)
        if isinstance(cmd, click.Group):
            yield from _all_command_paths(cmd, prefix + (name,))


def test_every_command_has_help():
    paths = list(_all_command_paths(cli))
    expected = {
        ("login",), ("logout",), ("whoami",), ("share",), ("shares", "list"), ("shares", "revoke"),
        ("received", "list"), ("received", "download"), ("documents", "list"), ("documents", "delete"),
        ("request-files",), ("requests", "list"), ("requests", "download"), ("requests", "close"),
    }
    assert expected <= set(paths)
    for path in paths:
        result = CliRunner().invoke(cli, [*path, "--help"])
        assert result.exit_code == 0, path


def test_closed_output_pipe_is_not_a_traceback(isolated_config):
    # `linshare whoami | head -1`: head closes the pipe while the tool is still writing
    script = (
        "from cli_anything.linshare import linshare_cli as c\n"
        "c.auth.whoami = lambda: {f'key{i}': 'x' * 80 for i in range(5000)}\n"
        "c.main()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, "whoami"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "LINSHARE_CONFIG_DIR": str(isolated_config)},
    )
    proc.stdout.read(10)
    proc.stdout.close()
    stderr = proc.stderr.read().decode()
    proc.wait(timeout=30)
    assert stderr == ""


def test_human_output_neutralises_terminal_control_characters():
    import click

    from cli_anything.linshare import linshare_cli as cli_mod
    from cli_anything.linshare.core.errors import ServerError

    @click.command()
    def fake():
        cli_mod.output([{"name": "a\x1b]0;pwned\x07b\x9b2J.pdf", "tags": ["x\x1b[2Jy"], "nested": {"k": "\x1b[31mred"}}])

    out = CliRunner().invoke(fake).output
    assert "\x1b" not in out and "\x07" not in out and "\x9b" not in out
    assert "a?]0;pwned?b?2J.pdf" in out

    @click.command()
    @cli_mod.handle_error
    def failing():
        raise ServerError("bad name \x1b[2J here")

    res = CliRunner().invoke(failing)
    assert "\x1b" not in res.output and "bad name ?[2J here" in res.output
