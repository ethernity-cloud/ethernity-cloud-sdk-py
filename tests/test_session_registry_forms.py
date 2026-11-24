"""session_registry.register, ecld-publish's registration of a securelock
session, against a JSON-RPC stand-in of one SessionRegistry and an IPFS add
stand-in on the loopback, as ethernity-cloud-sdk-js test/sessionRegistry.test.js
checks cas/session_registry.js. The registry answers its `rules` in one form:
the flat form (one measurement list, and RULES_FORM() reverting, as the
deployed registries answer, the bloxberg node with "Reverted 0x") or the
per-service form (a list per service, RULES_FORM() 2, ethernity-cas
contracts/SessionRegistry.sol). register takes the form the caller names
(enums.SESSION_RULES_FORM, 1 for every registry listed there): it posts the
body's measurements as the one list of the flat form, exactly as before, or
each service of the body's `services:` with its own list in the per-service
form; and it exits before it pins the body when the registry's RULES_FORM()
states another form, or when the form named is neither."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import rlp
from eth_account import Account
from eth_utils import keccak
from web3 import Web3

from ethernity_cloud_sdk_py.commands import enums
from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks
from ethernity_cloud_sdk_py.commands.pynithy import session_registry

REGISTRY = "0x00000000000000000000000000000000000000aa"
# Hardhat's first default account (the mnemonic "test test ... junk").
KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
CID = "bafkreigh2akiscaildcqabsyg3dfr6chu3fgpregiymsck7e7aqa4s52zy"
MR_A = "aa" * 32
MR_B = "bb" * 32
MR_C = "cc" * 32
TEMPLATE = (Path(session_registry.__file__).parent / "run" / "etny-securelock-test.yaml.tpl").read_text()
GETH_REVERT = {"code": 3, "message": "execution reverted", "data": "0x"}
PARITY_REVERT = {"code": -32015, "message": "VM execution error.", "data": "Reverted 0x"}


def securelock_body(name, mrs):
    """A securelock session body for `name` listing the measurements `mrs`,
    as publish.render_session_file renders the template."""
    return (TEMPLATE.replace("__PREDECESSOR__", "").replace("__MRENCLAVE__", '", "'.join(mrs))
            .replace("__ENCLAVE_NAME__", name).replace("__IGNORE_ADVISORIES__", '["*"]')
            .replace("__ESR_ENVIRONMENT__", "# ESR not enabled")).encode()


def two_services(name):
    """A body of two services, the second's measurements listed with a
    repeat, and an image entry and an environment key whose `name:` is no
    service's."""
    return f"""name: {name}
security:
  attestation:
    tolerate: [hyperthreading]
    ignore_advisories: ["*"]
services:
   - name: application
     mrenclaves: [ "{MR_A}" ]
     environment:
        name: not-a-service
   - name: executor
     mrenclaves: [ "{MR_B}", "{MR_C}", "{MR_B}" ]
images:
   - name: application_image
""".encode()


def selector(signature):
    return "0x" + keccak(text=signature)[:4].hex()


# The stand-in: a SessionRegistry at REGISTRY whose rules are in form
# `state["form"]` (1 answering RULES_FORM() with `state["revert"]`), holding
# no version, recording the registrations sent to it (`sent`, each decoded)
# and the IPFS adds (`adds`).
state = {"form": 1, "revert": GETH_REVERT, "sent": [], "adds": 0}


def _answer(method, params):
    if method == "eth_chainId":
        return "0x7a69"
    if method == "eth_estimateGas":
        return "0x100000"
    if method == "eth_getTransactionCount":
        return "0x0"
    if method == "eth_call":
        data = params[0]["data"]
        if data.startswith(selector("RULES_FORM()")):
            if state["form"] == 1:
                raise _Refusal(state["revert"])
            return "0x" + "00" * 31 + "02"
        if data.startswith(selector("latest(string)")):
            return "0x" + "00" * 32
        return "0x" + "00" * 31 + "01"
    if method == "eth_sendRawTransaction":
        raw = bytes.fromhex(params[0][2:])
        to, data = rlp.decode(raw)[3:6:2]
        assert "0x" + to.hex() == REGISTRY
        registry = Web3().eth.contract(abi=session_registry.abi_of(state["form"]))
        state["sent"].append(registry.decode_function_input(data))
        return "0x" + keccak(raw).hex()
    if method == "eth_getTransactionReceipt":
        return {"transactionHash": params[0], "transactionIndex": "0x0", "blockHash": "0x" + "11" * 32,
                "blockNumber": "0x1", "from": Account.from_key(KEY).address, "to": REGISTRY,
                "cumulativeGasUsed": "0x1", "gasUsed": "0x1", "effectiveGasPrice": "0x1",
                "contractAddress": None, "logs": [], "logsBloom": "0x" + "00" * 256, "status": "0x1",
                "type": "0x0"}
    raise _Refusal({"code": -32601, "message": f"no answer for {method}"})


class _Refusal(Exception):
    def __init__(self, error):
        super().__init__(error["message"])
        self.error = error


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.startswith("/api/v0/add"):
            state["adds"] += 1
            answer = {"Hash": CID}
        else:
            call = json.loads(body)
            try:
                answer = {"jsonrpc": "2.0", "id": call["id"], "result": _answer(call["method"], call["params"])}
            except _Refusal as e:
                answer = {"jsonrpc": "2.0", "id": call["id"], "error": e.error}
        out = json.dumps(answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture(scope="module")
def url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def register(url, body, rules_form, registry_form, revert=GETH_REVERT):
    """register of `body` in rules form `rules_form` against a registry in
    form `registry_form`, the stand-in's record reset first."""
    state.update(form=registry_form, revert=revert, sent=[], adds=0)
    network = SimpleNamespace(rpc_url=url, chain_id=31337, is_eip1559=False, gas_price=1)
    return session_registry.register(network, REGISTRY, rules_form, KEY, body, url)


def rules_sent():
    """(function name, rulesIn by field name) of the one transaction sent."""
    (function, args), = state["sent"]
    return function.fn_name, args["rulesIn"]


def services_of(rules):
    return [(s["name"], list(s["mrenclaves"])) for s in rules["services"]]


def test_form_2_posts_each_service_of_the_body_with_its_own_measurements(url):
    name, _, cid, registered = register(url, two_services("demo_SECURELOCK_amoy"), 2, 2)
    assert (name, cid, registered) == ("demo_SECURELOCK_amoy", CID, True)
    assert state["adds"] == 1
    fn_name, rules = rules_sent()
    assert fn_name == "register"
    assert services_of(rules) == [("application", [MR_A]), ("executor", [MR_B, MR_C])]
    assert (list(rules["tolerate"]), list(rules["ignoreAdvisories"])) == (["hyperthreading"], ["*"])


def test_form_2_posts_the_securelock_session_as_one_service_application(url):
    register(url, securelock_body("demo_SECURELOCK_amoy", [MR_A, MR_B]), 2, 2)
    _, rules = rules_sent()
    assert services_of(rules) == [("application", [MR_A, MR_B])]
    assert list(rules["tolerate"]) == ["hyperthreading", "outdated-tcb", "software-hardening-needed"]


@pytest.mark.parametrize("revert", [GETH_REVERT, PARITY_REVERT], ids=["geth", "bloxberg"])
def test_form_1_posts_the_one_list_of_the_flat_form_as_before(url, revert):
    _, _, _, registered = register(url, two_services("demo_SECURELOCK_amoy"), 1, 1, revert)
    assert registered is True
    _, rules = rules_sent()
    assert list(rules["mrenclaves"]) == [MR_A, MR_B, MR_C]
    flat = session_registry.parse_name_and_rules(two_services("demo_SECURELOCK_amoy").decode())[1]
    assert list(rules["mrenclaves"]) == flat[2]


@pytest.mark.parametrize("rules_form,registry_form", [(2, 1), (1, 2)])
def test_another_form_on_the_registry_stops_before_the_body_is_pinned(url, rules_form, registry_form):
    with pytest.raises(SystemExit, match=f"answers rules form {registry_form}, not the configured {rules_form}"):
        register(url, securelock_body("demo_SECURELOCK_amoy", [MR_A]), rules_form, registry_form)
    assert state["adds"] == 0, "nothing pinned"
    assert state["sent"] == [], "nothing sent"


@pytest.mark.parametrize("rules_form", [None, 0, 3, "2", True])
def test_a_form_that_is_neither_stops_before_the_body_is_pinned(url, rules_form):
    with pytest.raises(SystemExit, match="is not a form of a SessionRegistry's rules"):
        register(url, securelock_body("demo_SECURELOCK_amoy", [MR_A]), rules_form, 2)
    assert state["adds"] == 0


def test_the_rules_form_of_every_session_registry_listed_is_1_and_no_other():
    assert sorted(enums.SESSION_RULES_FORM) == sorted(enums.SESSION_REGISTRY_ADDRESSES)
    for network, form in enums.SESSION_RULES_FORM.items():
        assert form == 1, network
        assert BlockchainNetworks.get_session_rules_form(network) == 1
    assert BlockchainNetworks.get_session_rules_form("IOTEX_TESTNET") is None


def test_the_publish_passes_the_networks_rules_form_to_register():
    publish = (Path(session_registry.__file__).parent / "publish.py").read_text(encoding="utf-8")
    assert ("session_registry.register(\n            BLOCKCHAIN_CONFIG, registry_address,\n"
            "            BlockchainNetworks.get_session_rules_form(BLOCKCHAIN_NETWORK),\n"
            "            private_key, session_body,") in publish
