"""The session body's add carries the IPFS client's Authorization header, as
the image upload does. No network: the HTTP POST and the chain are replaced."""
from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks
from ethernity_cloud_sdk_py.commands.pynithy import session_registry
from ethernity_cloud_sdk_py.commands.pynithy.ipfs_client import IPFSClient

NETWORK = BlockchainNetworks.BLOXBERG_TESTNET
API = "http://ipfs.example:5001"
CID = "bafkreigh2akiscaildcqabsyg3dfr6chu3fgpregiymsck7e7aqa4s52zy"
REGISTRY = "0x" + "11" * 20
KEY = "0x" + "22" * 32
BODY = b"name: demo_SECURELOCK_V3_1\nservices:\n   - name: application\n     mrenclaves: [ \"a1\" ]\n"


class _Response:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"Hash": CID}


def _posts(monkeypatch):
    calls = []

    def post(url, files=None, headers=None, timeout=None):
        calls.append((url, headers))
        return _Response()

    monkeypatch.setattr(session_registry.requests, "post", post)
    return calls


def test_pin_body_sends_the_headers_with_the_add(monkeypatch):
    calls = _posts(monkeypatch)
    assert session_registry.pin_body(API, BODY, {"Authorization": "writer-token"}) == CID
    assert calls == [(f"{API}/api/v0/add?cid-version=1&raw-leaves=true&pin=true",
                      {"Authorization": "writer-token"})]


def test_pin_body_without_headers_sends_none(monkeypatch):
    calls = _posts(monkeypatch)
    session_registry.pin_body(API, BODY)
    assert calls[0][1] == {}


class _Account:
    address = "0x" + "33" * 20


class _Eth:
    account = type("A", (), {"from_key": staticmethod(lambda key: _Account())})()

    def contract(self, address, abi):
        return object()


class _Web3:
    eth = _Eth()


def test_register_pins_the_body_with_the_clients_header(monkeypatch):
    """register adds the body through the client's API with its headers; the
    chain already holds this body from this wallet, so nothing is sent."""
    pinned = []
    monkeypatch.setattr(session_registry, "_web3", lambda provider_url: _Web3())
    monkeypatch.setattr(session_registry, "latest_record",
                        lambda reg, name: (b"\x01" * 32, session_registry.session_hash(BODY), _Account.address))
    monkeypatch.setattr(session_registry, "pin_body",
                        lambda url, body, headers=None: pinned.append((url, body, headers)) or CID)
    client = IPFSClient(API, "writer-token")
    name, hash_hex, cid, registered = session_registry.register(
        NETWORK, REGISTRY, KEY, BODY, client.api_url,
        ipfs_headers=client.headers)
    assert (name, cid, registered) == ("demo_SECURELOCK_V3_1", CID, False)
    assert pinned == [(API, BODY, {"Authorization": "writer-token"})]


def test_a_client_without_a_token_adds_the_body_without_headers(monkeypatch):
    pinned = []
    monkeypatch.setattr(session_registry, "_web3", lambda provider_url: _Web3())
    monkeypatch.setattr(session_registry, "latest_record",
                        lambda reg, name: (b"\x01" * 32, session_registry.session_hash(BODY), _Account.address))
    monkeypatch.setattr(session_registry, "pin_body",
                        lambda url, body, headers=None: pinned.append(headers) or CID)
    client = IPFSClient(API, None)
    session_registry.register(NETWORK, REGISTRY, KEY, BODY, client.api_url,
                              ipfs_headers=client.headers)
    assert pinned == [{}]
