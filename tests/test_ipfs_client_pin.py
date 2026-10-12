"""A pin on an IPFS API is a `pin/add` carrying the client's Authorization
header, reported true when the API names the CID. No network: the HTTP POST
is replaced."""
from ethernity_cloud_sdk_py.commands.pynithy import ipfs_client as module
from ethernity_cloud_sdk_py.commands.pynithy.ipfs_client import IPFSClient

API = "https://ipfs.example"
CID = "QmbohHhXrfkpnzg7uGQ1LHRA1jXD6K12PWTkpiRGXUmPa1"


class _Response:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def _posts(monkeypatch, response):
    calls = []

    def post(url, headers=None, timeout=None, **kwargs):
        calls.append((url, headers, timeout))
        return response

    monkeypatch.setattr(module.requests, "post", post)
    return calls


def test_a_pin_is_a_recursive_pin_add_with_the_token(monkeypatch):
    calls = _posts(monkeypatch, _Response(200, '{"Pins":["%s"]}' % CID))
    assert IPFSClient(API, "writer-token").pin(CID, timeout_secs=120) is True
    assert calls == [(f"{API}/api/v0/pin/add?arg={CID}&recursive=true", {"Authorization": "writer-token"}, 120)]


def test_a_refused_pin_is_false(monkeypatch, capsys):
    _posts(monkeypatch, _Response(403, "writes need a token"))
    assert IPFSClient(API, None).pin(CID) is False
    assert "status 403" in capsys.readouterr().out


def test_an_answer_without_the_cid_is_false(monkeypatch):
    _posts(monkeypatch, _Response(200, '{"Pins":[]}'))
    assert IPFSClient(API, "writer-token").pin(CID) is False
