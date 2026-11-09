import responses

from cli_anything.linshare.core.received import download_received, list_received
from cli_anything.linshare.utils.linshare_backend import API_PATH, LinShareClient

SERVER = "https://ls.example.com"
BASE = SERVER + API_PATH


def client():
    return LinShareClient(SERVER, token="t")


@responses.activate
def test_list_received_projection():
    responses.get(
        BASE + "/received_shares",
        json=[
            {
                "uuid": "r1",
                "name": "a.pdf",
                "size": 3,
                "sender": {"mail": "boss@linagora.com", "firstName": "B"},
                "creationDate": 1793750400000,
                "expirationDate": None,
            }
        ],
    )
    assert list_received(client()) == [
        {
            "uuid": "r1",
            "name": "a.pdf",
            "size": 3,
            "sender": "boss@linagora.com",
            "creationDate": "2026-11-04T00:00:00Z",
            "expirationDate": None,
        }
    ]


@responses.activate
def test_download_does_not_overwrite(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"original")
    responses.get(BASE + "/received_shares/r1", json={"uuid": "r1", "name": "a.pdf"})
    responses.get(BASE + "/received_shares/r1/download", body=b"new content")
    result = download_received(client(), "r1", tmp_path)
    assert result == {"uuid": "r1", "path": str(tmp_path / "a (1).pdf")}
    assert (tmp_path / "a.pdf").read_bytes() == b"original"
    assert (tmp_path / "a (1).pdf").read_bytes() == b"new content"


@responses.activate
def test_download_strips_path_from_server_name(tmp_path):
    responses.get(BASE + "/received_shares/r1", json={"uuid": "r1", "name": "../../etc/passwd"})
    responses.get(BASE + "/received_shares/r1/download", body=b"x")
    assert download_received(client(), "r1", tmp_path)["path"] == str(tmp_path / "passwd")
