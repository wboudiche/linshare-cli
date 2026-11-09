import json

import pytest
import requests
import responses

from cli_anything.linshare.core.errors import (
    AuthError,
    BusinessError,
    ServerError,
    plain_message,
)
from cli_anything.linshare.utils.linshare_backend import API_PATH, LinShareClient

SERVER = "https://ls.example.com"
BASE = SERVER + API_PATH


@responses.activate
def test_bearer_and_provider_headers_sent():
    responses.get(BASE + "/documents", json=[])
    LinShareClient(SERVER, token="tok").get("/documents")
    headers = responses.calls[0].request.headers
    assert headers["Authorization"] == "Bearer tok"
    assert headers["X-LinShare-Auth-Provider"] == "Jwt"
    assert headers["Accept"] == "application/json"


@responses.activate
def test_basic_auth_and_totp_header():
    responses.get(BASE + "/authentication/version", json={"version": "6.5.4"})
    LinShareClient(SERVER, basic=("me@example.com", "pw"), totp="123456").get("/authentication/version")
    headers = responses.calls[0].request.headers
    assert headers["Authorization"].startswith("Basic ")
    assert headers["X-LinShare-2fa-pin"] == "123456"
    assert "X-LinShare-Auth-Provider" not in headers


@responses.activate
def test_401_token_rejected_is_auth_error():
    responses.get(BASE + "/documents", status=401)
    with pytest.raises(AuthError) as exc:
        LinShareClient(SERVER, token="old").get("/documents")
    assert exc.value.exit_code == 2
    assert "linshare login" in exc.value.message
    assert exc.value.http_status == 401


@pytest.mark.parametrize("code", ["1002", "1003"])
@responses.activate
def test_401_with_totp_code_is_totp_required(code):
    responses.get(BASE + "/authentication/version", status=401, headers={"X-LinShare-Auth-error-code": code})
    with pytest.raises(AuthError) as exc:
        LinShareClient(SERVER, basic=("me@example.com", "pw")).get("/authentication/version")
    assert exc.value.code == "totp_required"


@responses.activate
def test_401_bad_password_message():
    responses.get(BASE + "/authentication/version", status=401, headers={"X-LinShare-Auth-error-code": "1000"})
    with pytest.raises(AuthError) as exc:
        LinShareClient(SERVER, basic=("me@example.com", "bad")).get("/authentication/version")
    assert exc.value.code == "auth"
    assert "password" in exc.value.message.lower()


@responses.activate
def test_business_error_maps_errcode():
    responses.post(BASE + "/shares", status=400, json={"errCode": 1234, "message": "x"})
    with pytest.raises(BusinessError) as exc:
        LinShareClient(SERVER, token="t").post_json("/shares", {})
    assert exc.value.linshare_error_code == 1234
    assert exc.value.exit_code == 3
    assert exc.value.message == "x"


@responses.activate
def test_known_business_error_gets_plain_message():
    responses.post(BASE + "/shares", status=403, json={"errCode": 33423, "message": "raw"})
    with pytest.raises(BusinessError) as exc:
        LinShareClient(SERVER, token="t").post_json("/shares", {})
    assert exc.value.message == plain_message(33423, "raw")
    assert "outside" in exc.value.message


def test_plain_message_unknown_code_passes_server_message():
    assert plain_message(99999, "server says no") == "server says no"


@responses.activate
def test_500_is_server_error():
    responses.get(BASE + "/documents", status=500, body="boom")
    with pytest.raises(ServerError) as exc:
        LinShareClient(SERVER, token="t").get("/documents")
    assert exc.value.exit_code == 4


@responses.activate
def test_connection_error_is_server_error():
    responses.get(BASE + "/documents", body=requests.ConnectionError("down"))
    with pytest.raises(ServerError) as exc:
        LinShareClient(SERVER, token="t").get("/documents")
    assert SERVER in exc.value.message


@responses.activate
def test_post_json_sends_body_and_params():
    responses.post(BASE + "/upload_request_groups", json={"uuid": "g1"})
    out = LinShareClient(SERVER, token="t").post_json(
        "/upload_request_groups", {"label": "L"}, params={"collective": "false"}
    )
    assert out == {"uuid": "g1"}
    call = responses.calls[0]
    assert "collective=false" in call.request.url
    assert json.loads(call.request.body) == {"label": "L"}


@responses.activate
def test_empty_response_body_returns_none():
    responses.delete(BASE + "/shares/s1", status=204)
    assert LinShareClient(SERVER, token="t").delete("/shares/s1") is None


@responses.activate
def test_upload_sends_file_filesize_filename(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"12345")
    responses.post(BASE + "/documents", json={"uuid": "d1", "name": "report.pdf", "size": 5})
    LinShareClient(SERVER, token="t").upload("/documents", f, {"description": ""})
    body = responses.calls[0].request.body
    assert b'name="filesize"' in body and b"\r\n\r\n5\r\n" in body
    assert b'name="file"; filename="report.pdf"' in body
    # The separate `filename` text field is decoded wrongly by LinShare 6.5 and 7.0
    # (non-ASCII names become U+FFFD); the file part's own filename is decoded correctly.
    assert b'name="filename"' not in body


@responses.activate
def test_download_streams_to_target(tmp_path):
    responses.get(BASE + "/received_shares/r1/download", body=b"content")
    target = tmp_path / "out.bin"
    assert LinShareClient(SERVER, token="t").download("/received_shares/r1/download", target) == target
    assert target.read_bytes() == b"content"
