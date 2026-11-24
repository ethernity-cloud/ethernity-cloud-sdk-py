"""ImageRegistry.record_epc_need against a JSON-RPC stand-in of an image
registry on the loopback: ECImageRegistryV4 answers imageEpcUnits(ipfsHash)
with the EPC need it records for a securelock image (0 for none) and takes
setImageEpcUnits once (pox-smart-contract-litvm ECImageRegistryV4.sol; D200,
OD24); a registry without the function (V1 to V3) reverts the read, the
bloxberg node answering "Reverted 0x", a geth node "execution reverted"; a
node that does not answer is an error. ecld-publish (pynithy/publish.py)
refuses an ECLD_EPC_MIB that is not a whole number of 1 to 4,194,240 MiB at
its start and records the need after the registration and before the
certificate (tests/test_publish_build_gate.py). The rules and the words are
ethernity-cloud-sdk-js registryEpc.js's (test/registryEpc.test.js)."""
import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
from eth_abi import decode, encode
from eth_utils import keccak
from web3 import Web3

from ethernity_cloud_sdk_py.commands.config import Config

REGISTRY = "0x00000000000000000000000000000000000000aa"
HASH = "QmImage"
GETH_REVERT = {"code": 3, "message": "execution reverted", "data": "0x"}
PARITY_REVERT = {"code": -32015, "message": "VM execution error.", "data": "Reverted 0x"}
SELECTOR = "0x" + keccak(text="imageEpcUnits(string)")[:4].hex()

# What the stand-in answers imageEpcUnits with: a number, a revert's error,
# or "down" (HTTP 503, no JSON-RPC answer); the hashes it was asked for.
state = {"answer": 0, "reads": []}


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
            data = call["params"][0]["data"]
            assert data[:10] == SELECTOR
            state["reads"].append(decode(["string"], bytes.fromhex(data[10:]))[0])
            if state["answer"] == "down":
                self.send_response(503)
                self.end_headers()
                return
            if isinstance(state["answer"], dict):
                answer = {"jsonrpc": "2.0", "id": call["id"], "error": state["answer"]}
            else:
                answer = {"jsonrpc": "2.0", "id": call["id"], "result": "0x" + encode(["uint16"], [state["answer"]]).hex()}
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
def module(monkeypatch, tmp_path):
    """The registry module with its config over a .config.json of the
    bloxberg testnet in tmp_path, the CWD: the module binds its config to
    the CWD's .config.json when it is first imported."""
    (tmp_path / ".config.json").write_text(json.dumps({
        "PROJECT_NAME": "demo", "BLOCKCHAIN_NETWORK": "BLOXBERG_TESTNET", "DAPP_TYPE": "Pynithy", "VERSION": 1}),
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    found = importlib.import_module("ethernity_cloud_sdk_py.commands.pynithy.run.image_registry")
    cfg = Config(tmp_path / ".config.json")
    cfg.load()
    monkeypatch.setattr(found, "config", cfg)
    return found


@pytest.fixture
def registry(url, module):
    """An ImageRegistry of `module` reading the stand-in at REGISTRY; its
    sends are kept in `sent` (the function's name and arguments) and not
    signed."""
    found = module.ImageRegistry()
    found.provider = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 5}))
    found.image_registry_address = REGISTRY
    found.image_registry_contract = found.provider.eth.contract(address=Web3.to_checksum_address(REGISTRY),
                                                                abi=found.image_registry_abi)
    found.sent = []
    found._send = lambda builder, gas_limit, label: found.sent.append((builder.fn_name, builder.args))
    return found


def record(registry, answer, units):
    state.update(answer=answer, reads=[])
    registry.sent.clear()
    return registry.record_epc_need(HASH, units)


def test_ecld_epc_mib_is_the_need_in_mib_rounded_up_to_units_of_64_mib_one_unit_when_unset(module):
    assert [module.epc_units_of_setting(t) for t in (None, "", "1", "64", "65", " 130 ", str(module.EPC_MAX_MIB))] == [
        1, 1, 1, 1, 2, 3, 65535]


@pytest.mark.parametrize("text", ["0", "4194241", "-1", "1.5", "0x40", "64M", "abc", "+64", "٦٤"])
def test_an_ecld_epc_mib_that_is_not_a_whole_number_of_1_to_4194240_is_refused(module, text):
    with pytest.raises(ValueError, match="ECLD_EPC_MIB is the EPC the securelock needs, in MiB: a whole number of 1 "
                                         "to 4194240"):
        module.epc_units_of_setting(text)


def test_a_need_above_one_unit_is_written_once_on_a_registry_that_records_none(registry):
    told = record(registry, 0, 3)
    assert registry.sent == [("setImageEpcUnits", (HASH, 3))] and state["reads"] == [HASH]
    assert told == f"The EPC need of {HASH} is recorded: 3 units of 64 MiB (192 MiB)."


def test_a_need_of_one_unit_records_none(registry):
    assert record(registry, 0, 1) == "" and registry.sent == []


def test_a_need_recorded_already_and_equal_to_the_publish_s_is_left_as_it_is(registry):
    assert record(registry, 3, 3) == f"The EPC need of {HASH} is recorded: 3 units of 64 MiB."
    assert registry.sent == []


@pytest.mark.parametrize("recorded,units", [(3, 2), (3, 4), (5, 1)])
def test_a_need_recorded_already_that_differs_ends_the_publish_and_writes_nothing(registry, recorded, units):
    with pytest.raises(Exception) as refused:
        record(registry, recorded, units)
    assert str(refused.value) == (
        f"the image registry {REGISTRY} records an EPC need of {recorded} units of 64 MiB ({recorded * 64} MiB) for "
        f"{HASH}, and this publish states {units} ({units * 64} MiB, ECLD_EPC_MIB): the need is written once. Set "
        f"ECLD_EPC_MIB to {recorded * 64}, or publish a new image.")
    assert registry.sent == []


@pytest.mark.parametrize("revert", [GETH_REVERT, PARITY_REVERT], ids=["geth", "bloxberg"])
def test_a_registry_without_image_epc_units_records_nothing_and_tells_of_a_need_above_one_unit(registry, revert):
    assert record(registry, revert, 3) == (
        f"The image registry {REGISTRY} records no EPC need (imageEpcUnits reverts): the need of 3 units of 64 MiB "
        "(ECLD_EPC_MIB) is not recorded.")
    assert registry.sent == []
    assert record(registry, revert, 1) == "" and registry.sent == []


def test_a_read_the_node_does_not_answer_raises_and_nothing_is_written(registry):
    with pytest.raises(requests.exceptions.HTTPError):
        record(registry, "down", 3)
    assert registry.sent == []


def test_the_step_of_each_recorded_need(module):
    assert [module.epc_step(r, u) for r, u in ((None, 3), (0, 3), (0, 1), (3, 3), (1, 1), (3, 1))] == [
        "none", "write", "none", "keep", "keep", "stop"]
