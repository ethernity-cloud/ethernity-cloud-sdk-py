"""ImageRegistry.seeding_open against a JSON-RPC stand-in of an image registry
on the loopback: ECImageRegistryV4 answers seedingOpen() true while it imports
the owners of the securelock names and refuses registerImage and addImage
with SeedingOpen() (pox-smart-contract-litvm ECImageRegistryV4.sol); a
registry without seeding (V1 to V3) reverts the call, the bloxberg node
answering "Reverted 0x", a geth node "execution reverted"; a node that does
not answer is an error. ecld-publish (pynithy/publish.py main) reads it
first and stops before anything is started while the registry is seeding or
its answer cannot be read (tests/test_publish_build_gate.py)."""
import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
from eth_utils import keccak
from web3 import Web3

from ethernity_cloud_sdk_py.commands.config import Config

REGISTRY = "0x00000000000000000000000000000000000000aa"
GETH_REVERT = {"code": 3, "message": "execution reverted", "data": "0x"}
PARITY_REVERT = {"code": -32015, "message": "VM execution error.", "data": "Reverted 0x"}

# What the stand-in answers seedingOpen() with: True, False, a revert's
# error, or "down" (HTTP 503, no JSON-RPC answer).
state = {"answer": True, "calls": 0}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        call = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if call["method"] == "eth_chainId":
            answer = {"jsonrpc": "2.0", "id": call["id"], "result": "0x7a69"}
        else:
            assert call["method"] == "eth_call"
            assert call["params"][0]["to"].lower() == REGISTRY
            assert call["params"][0]["data"] == "0x" + keccak(text="seedingOpen()")[:4].hex()
            state["calls"] += 1
            if state["answer"] == "down":
                self.send_response(503)
                self.end_headers()
                return
            if isinstance(state["answer"], dict):
                answer = {"jsonrpc": "2.0", "id": call["id"], "error": state["answer"]}
            else:
                answer = {"jsonrpc": "2.0", "id": call["id"], "result": "0x" + "00" * 31 + ("01" if state["answer"] else "00")}
        out = json.dumps(answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture(scope="module")
def url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def registry(monkeypatch, tmp_path, url):
    """An ImageRegistry of the bloxberg testnet over a .config.json in
    tmp_path, the CWD, reading the stand-in at REGISTRY."""
    (tmp_path / ".config.json").write_text(json.dumps({
        "PROJECT_NAME": "demo", "BLOCKCHAIN_NETWORK": "BLOXBERG_TESTNET", "DAPP_TYPE": "Pynithy", "VERSION": 1}),
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    module = importlib.import_module("ethernity_cloud_sdk_py.commands.pynithy.run.image_registry")
    cfg = Config(tmp_path / ".config.json")
    cfg.load()
    monkeypatch.setattr(module, "config", cfg)
    found = module.ImageRegistry()
    found.provider = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 5}))
    found.image_registry_address = REGISTRY
    return found


def read(registry, answer):
    state.update(answer=answer, calls=0)
    return registry.seeding_open()


def test_a_registry_seeding_its_name_owners_answers_true_one_that_closed_seeding_false(registry):
    assert read(registry, True) is True
    assert state["calls"] == 1
    assert read(registry, False) is False


@pytest.mark.parametrize("revert", [GETH_REVERT, PARITY_REVERT], ids=["geth", "bloxberg"])
def test_a_registry_without_seeding_open_takes_registrations(registry, revert):
    assert read(registry, revert) is False


def test_a_read_the_node_does_not_answer_raises(registry):
    with pytest.raises(requests.exceptions.HTTPError):
        read(registry, "down")


def test_the_refusal_names_the_registry_seeding_open_and_seeding_open_error():
    module = importlib.import_module("ethernity_cloud_sdk_py.commands.pynithy.run.image_registry")
    text = module.seeding_refusal(REGISTRY)
    assert REGISTRY in text and "seedingOpen() answers true" in text and "SeedingOpen()" in text
