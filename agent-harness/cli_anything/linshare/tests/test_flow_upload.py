"""Chunked ("flow") upload of large files: the same protocol as the LinShare web UI."""

import email
import email.policy

import pytest
import requests
import responses

from cli_anything.linshare.core.documents import upload_document
from cli_anything.linshare.core.errors import BusinessError, ServerError
from cli_anything.linshare.utils import linshare_backend
from cli_anything.linshare.utils.linshare_backend import API_PATH, LinShareClient, flow_chunk_size

SERVER = "https://ls.example.com"
BASE = SERVER + API_PATH
MiB = 1024 * 1024


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(linshare_backend.time, "sleep", lambda s: None)


def client():
    return LinShareClient(SERVER, token="t")


def parts(request) -> dict:
    """Multipart body of a mocked request → {field name: bytes}."""
    raw = b"Content-Type: " + request.headers["Content-Type"].encode() + b"\r\n\r\n" + request.body
    msg = email.message_from_bytes(raw, policy=email.policy.HTTP)
    return {p.get_param("name", header="content-disposition"): p.get_payload(decode=True) for p in msg.iter_parts()}


def flow_calls():
    return [c for c in responses.calls if c.request.url.startswith(BASE + "/flow")]


def big_file(tmp_path, size, name="big été.bin"):
    f = tmp_path / name
    f.write_bytes(bytes(i % 251 for i in range(size)))
    return f


PENDING = {"chunkUploadSuccess": True, "lastChunk": False}
DONE = {"chunkUploadSuccess": True, "lastChunk": True, "entry": {"uuid": "d1", "name": "big été.bin", "size": 5 * MiB}}


@pytest.mark.parametrize(
    "size, expected",
    [
        (1, 2 * MiB),  # never below 2 MiB
        (300_000_000, 6 * MiB),  # 2 % of the size, rounded up to whole MiB
        (10 * 1024 * MiB, 100 * MiB),  # never above 100 MiB
    ],
)
def test_flow_chunk_size_follows_the_web_ui(size, expected):
    assert flow_chunk_size(size) == expected


@responses.activate
def test_small_file_is_sent_in_one_request(tmp_path):
    f = big_file(tmp_path, 2 * MiB, "small.bin")
    responses.post(BASE + "/documents", json={"uuid": "d0", "name": "small.bin", "size": 2 * MiB})
    assert upload_document(client(), f)["uuid"] == "d0"
    assert flow_calls() == []


@responses.activate
def test_large_file_is_sent_in_chunks(tmp_path):
    f = big_file(tmp_path, 5 * MiB)
    responses.post(BASE + "/flow", json=PENDING)
    responses.post(BASE + "/flow", json=PENDING)
    responses.post(BASE + "/flow", json=DONE)

    doc = upload_document(client(), f)

    assert doc == {"uuid": "d1", "name": "big été.bin", "size": 5 * MiB}
    sent = [parts(c.request) for c in flow_calls()]
    assert [p["flowChunkNumber"] for p in sent] == [b"1", b"2", b"3"]
    assert {p["flowTotalChunks"] for p in sent} == {b"3"}
    assert {p["flowChunkSize"] for p in sent} == {str(2 * MiB).encode()}
    assert {p["flowTotalSize"] for p in sent} == {str(5 * MiB).encode()}
    assert [p["flowCurrentChunkSize"] for p in sent] == [str(n).encode() for n in (2 * MiB, 2 * MiB, MiB)]
    assert len({p["flowIdentifier"] for p in sent}) == 1
    assert {p["flowFilename"] for p in sent} == {"big été.bin".encode()}
    assert b"".join(p["file"] for p in sent) == f.read_bytes()


@responses.activate
def test_refused_chunk_is_a_business_error_and_stops(tmp_path):
    f = big_file(tmp_path, 5 * MiB)
    responses.post(BASE + "/flow", json={"chunkUploadSuccess": False, "errorMessage": "quota", "errCode": 46010})
    with pytest.raises(BusinessError) as exc:
        upload_document(client(), f)
    assert exc.value.linshare_error_code == 46010
    assert len(flow_calls()) == 1


@responses.activate
def test_middle_chunk_is_retried_after_a_server_error(tmp_path):
    f = big_file(tmp_path, 5 * MiB)
    responses.post(BASE + "/flow", json=PENDING)
    responses.post(BASE + "/flow", status=503)
    responses.post(BASE + "/flow", body=requests.ConnectionError("reset"))
    responses.post(BASE + "/flow", json=PENDING)
    responses.post(BASE + "/flow", json=DONE)

    assert upload_document(client(), f)["uuid"] == "d1"
    numbers = [parts(c.request)["flowChunkNumber"] for c in flow_calls() if c.request.body]
    assert numbers == [b"1", b"2", b"2", b"2", b"3"]


@responses.activate
def test_middle_chunk_gives_up_after_three_retries(tmp_path):
    f = big_file(tmp_path, 5 * MiB)
    responses.post(BASE + "/flow", json=PENDING)
    for _ in range(4):
        responses.post(BASE + "/flow", status=503)
    with pytest.raises(ServerError):
        upload_document(client(), f)
    assert len(flow_calls()) == 5  # chunk 1, then chunk 2 tried 1 + 3 times


@responses.activate
def test_last_chunk_is_never_resent(tmp_path):
    # the server may already have created the document: resending could not complete it anyway
    f = big_file(tmp_path, 5 * MiB)
    responses.post(BASE + "/flow", json=PENDING)
    responses.post(BASE + "/flow", json=PENDING)
    responses.post(BASE + "/flow", body=requests.ReadTimeout("slow"))
    with pytest.raises(ServerError) as exc:
        upload_document(client(), f)
    assert exc.value.code == "timeout"
    assert len(flow_calls()) == 3


@responses.activate
def test_last_chunk_without_a_document_is_an_error(tmp_path):
    f = big_file(tmp_path, 5 * MiB)
    responses.post(BASE + "/flow", json=PENDING)
    responses.post(BASE + "/flow", json=PENDING)
    responses.post(BASE + "/flow", json=PENDING)
    with pytest.raises(ServerError, match="did not finish"):
        upload_document(client(), f)
