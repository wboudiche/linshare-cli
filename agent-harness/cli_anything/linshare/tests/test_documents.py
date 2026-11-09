import pytest
import responses

from cli_anything.linshare.core.documents import delete_documents, list_documents, upload_document
from cli_anything.linshare.core.errors import BusinessError, UsageError
from cli_anything.linshare.utils.linshare_backend import API_PATH, LinShareClient

SERVER = "https://ls.example.com"
BASE = SERVER + API_PATH


def client():
    return LinShareClient(SERVER, token="t")


@responses.activate
def test_upload_keeps_non_ascii_filename(tmp_path):
    f = tmp_path / "Contrat été 2026.pdf"
    f.write_bytes(b"abc")
    responses.post(BASE + "/documents", json={"uuid": "d1", "name": "Contrat été 2026.pdf", "size": 3})
    assert upload_document(client(), f) == {"uuid": "d1", "name": "Contrat été 2026.pdf", "size": 3}
    body = responses.calls[0].request.body
    assert "Contrat été 2026.pdf".encode() in body


@responses.activate
def test_upload_missing_file_is_usage_error_without_http(tmp_path):
    with pytest.raises(UsageError) as exc:
        upload_document(client(), tmp_path / "nope.pdf")
    assert "nope.pdf" in exc.value.message
    assert len(responses.calls) == 0


@responses.activate
def test_upload_directory_is_usage_error(tmp_path):
    with pytest.raises(UsageError):
        upload_document(client(), tmp_path)


@responses.activate
def test_list_documents_projection():
    responses.get(
        BASE + "/documents",
        json=[
            {
                "uuid": "d1",
                "name": "a.pdf",
                "size": 10,
                "creationDate": 1793750400000,
                "expirationDate": None,
                "sha256sum": "x",
            }
        ],
    )
    assert list_documents(client()) == [
        {"uuid": "d1", "name": "a.pdf", "size": 10, "creationDate": "2026-11-04T00:00:00Z", "expirationDate": None}
    ]


@responses.activate
def test_delete_reports_partial_progress():
    responses.delete(BASE + "/documents/d1", json={"uuid": "d1"})
    responses.delete(BASE + "/documents/d2", status=404, json={"errCode": 9000, "message": "not found"})
    with pytest.raises(BusinessError) as exc:
        delete_documents(client(), ["d1", "d2", "d3"])
    assert "d2" in exc.value.message
    assert "already deleted: d1" in exc.value.message
    assert len(responses.calls) == 2


@responses.activate
def test_delete_all_ok():
    responses.delete(BASE + "/documents/d1", json={})
    assert delete_documents(client(), ["d1"]) == [{"uuid": "d1", "deleted": True}]
