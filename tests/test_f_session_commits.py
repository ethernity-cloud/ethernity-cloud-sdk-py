"""The step commit hold and the output envelope (PHASE-D-PLAN.md F.2) against
tests/spec/session-commits.json: the fixture's ESR wallet signs over the
registry's commitDigest (chain 31337, registry 0x1dc5...) through esr_local
with the key hashes, CIDs, versions and nonces of the fixture, so a
two-commit step produces exactly the fixture's two session.commit files and
an envelope byte-identical to envelopeJson; nothing of the step is in the
bucket before its session.output.<n>; a step without commits emits the
empty lists; esr.authorizations.json is the concatenation of the staged
steps' records after each envelope; the fence's file keeps the esr.commit
name beside them and stays out of that file; an ecld-test run (local_api)
stages a plain task's commit at once."""
import json
from types import SimpleNamespace

import pytest
from eth_abi import encode
from eth_account import Account

from tests.f_session_support import BUCKET, ORDER_ID, SPEC, FakeApp, content, load_modules, names

F = json.loads((SPEC / "session-commits.json").read_text(encoding="utf-8"))
WITH = F["steps"]["withCommits"]
WITHOUT = F["steps"]["withoutCommits"]
RECORDS = WITH["envelope"]["esr"]["commits"]
CALLER = RECORDS[0]["callerUsed"]
KEYS = {"a": bytes.fromhex(RECORDS[0]["keyHash"][2:]), "b": bytes.fromhex(RECORDS[1]["keyHash"][2:])}
PRIV = bytes.fromhex(F["esr"]["enclaveWalletPrivateKey"][2:])
WALLET = Account.from_key(PRIV).address

PAYLOAD = """
from ecld_state import StateRegistry
def on_input(data):
    reg = StateRegistry()
    if data == "update":
        reg.commit("a", lambda s: {"balance": 2}, nonce=8)
        reg.commit("b", lambda s: {"n": 1}, nonce=1)
        return "balance updated"
    return "read only"
ecld.on_input = on_input
"""
PLAIN_PAYLOAD = """
from ecld_state import StateRegistry
StateRegistry().commit("counter", lambda s: {"n": 1})
___etny_result___("committed")
"""


def _keccak(data):
    from Crypto.Hash import keccak
    h = keccak.new(digest_bits=256)
    h.update(data)
    return h.digest()


def commit_digest(enclave, key_hash, cid, expected_version, relay_nonce, nonce):
    """EnclaveStateRegistry.commitDigest: keccak256(abi.encode(...))."""
    return _keccak(encode(
        ["string", "uint256", "address", "address", "bytes32", "bytes32", "uint256", "uint256", "uint256"],
        ["EnclaveStateRegistry.commitFor", F["chainId"], F["registry"], enclave, key_hash,
         _keccak(cid.encode("utf-8")), expected_version, relay_nonce, nonce]))


class CidQueue:
    """unixfs_cid as the fixture's CIDs: a new blob takes the next queued
    CID, a blob seen before answers its own."""

    def __init__(self, cids):
        self.cids = list(cids)
        self.known = {}

    def __call__(self, blob, block_size=None):
        if blob not in self.known:
            self.known[blob] = self.cids.pop(0)
        return self.known[blob]


@pytest.fixture
def env(monkeypatch):
    m = load_modules(monkeypatch)
    ecld_state, esr_local, esr_wallet, exec_mod = m["ecld_state"], m["esr_local"], m["esr_wallet"], m["etny_session_exec"]
    swift = esr_local.MemSwift()
    reg = esr_local.MemRegistry(address=F["registry"])
    web3 = esr_local.MemWeb3(reg)
    # Key a at version 7, relay nonce 7, nonce 7; key b never committed.
    for version in range(7):
        reg.apply_commit(WALLET, KEYS["a"], f"bafkreiprev{version}", version, 0)
    monkeypatch.setattr(esr_local._Functions, "commitDigest",
                        lambda self, enclave, kh, cid, ev, rn, nonce=0:
                        esr_local._Callable(commit_digest(enclave, kh, cid, ev, rn, nonce)))
    monkeypatch.setattr(esr_wallet, "_derive_wallet_private_key", lambda identity: PRIV)
    monkeypatch.setattr(esr_wallet, "derive_wallet_address", lambda identity: WALLET)
    monkeypatch.setattr(ecld_state, "_key_hash", lambda key: KEYS[key])
    cids = CidQueue([RECORDS[0]["cid"], RECORDS[1]["cid"], F["fence"]["content"] and json.loads(F["fence"]["content"])["cid"],
                     "bafkreifenceofkeybxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"])
    monkeypatch.setattr(ecld_state, "unixfs_cid", cids)
    acl = ecld_state._new_acl(CALLER)

    def read_container(self, key, default=None, block_identifier=None):
        if key == "a":
            return dict(acl), {"balance": 1}, 7, "bafkreiprev6"
        return None, {}, 0, None
    monkeypatch.setattr(ecld_state.StateRegistry, "_read_container", read_container)
    ecld_state.configure(identity_priv=b"ecld-local-test-identity-key-0001", swift_stream_service=swift,
                         bucket=BUCKET, contract_address=F["registry"], web3=web3, caller=CALLER)
    ecld_state.set_local_commit_apply(
        lambda enclave, kh, cid, ev, nonce=0: (reg.land_at(reg.block_number + 1),
                                               reg.apply_commit(enclave, kh, cid, ev, nonce)))
    app = FakeApp(swift, caller=CALLER, payload=PAYLOAD)
    app.stage("session.config.securelock", json.dumps(
        {"order_id": ORDER_ID, "close_after_seconds": 60, "input_cutoff_seconds": 60, "max_messages": 256}))
    session = exec_mod.SecureLockSession(app)
    assert session._initial_run() is None
    session.emitted = WITH["outputObject"]
    return SimpleNamespace(ecld_state=ecld_state, swift=swift, reg=reg, app=app, session=session, cids=cids)


def test_nothing_of_a_step_is_in_the_bucket_before_its_output(env):
    outcome = env.session._handle(WITH["envelope"]["ack"], "update")
    assert outcome == (0, "balance updated")
    assert len(env.ecld_state._held) == 2
    assert names(env.swift, "session.commit.") == [] and names(env.swift, "esr.") == []
    assert env.app.pushed_names() == []
    assert env.reg.state(WALLET, KEYS["a"])[1] == 7


def test_a_two_commit_step_stages_exactly_the_fixtures_files_and_envelope(env):
    env.session._emit(WITH["envelope"]["ack"], *env.session._handle(WITH["envelope"]["ack"], "update"))
    assert names(env.swift, "session.commit.") == sorted(f["name"] for f in WITH["commitFiles"])
    for f in WITH["commitFiles"]:
        assert content(env.swift, f["name"]) == f["content"].encode("utf-8")
    assert env.app.pushed_names() == ["session.output.3"]
    assert env.app.pushed_data("session.output.3") == WITH["envelopeJson"]
    assert env.app.pushed[0][2] == f"k7p2x9qv4m1zr8t3hw6n:{ORDER_ID}:session.output.3:"
    assert json.loads(env.app.pushed_data("session.output.3"))["commits"] == WITH["envelope"]["commits"]
    assert content(env.swift, "esr.authorizations.json") == F["authorizations"]["afterSteps"][0]["content"].encode()
    assert names(env.swift, "esr.commit.") == []
    assert env.ecld_state._held == [] and env.ecld_state.ledger_attestation()["commits"] == RECORDS
    assert env.reg.state(WALLET, KEYS["a"])[1] == 8 and env.reg.state(WALLET, KEYS["b"])[1] == 1


def test_a_step_without_commits_emits_the_empty_lists(env):
    env.session._emit(WITH["envelope"]["ack"], *env.session._handle(WITH["envelope"]["ack"], "update"))
    env.session._emit(WITHOUT["envelope"]["ack"], *env.session._handle(WITHOUT["envelope"]["ack"], "read"))
    assert env.app.pushed_data("session.output.4") == WITHOUT["envelopeJson"]
    assert names(env.swift, "session.commit.") == sorted(f["name"] for f in WITH["commitFiles"])
    assert content(env.swift, "esr.authorizations.json") == F["authorizations"]["afterSteps"][1]["content"].encode()


def test_a_non_esr_session_envelope_carries_null_wallet_and_empty_lists(env, monkeypatch):
    monkeypatch.setattr(env.session, "esr", False)
    env.ecld_state._held = None
    env.session.emitted = 4
    env.session._emit(4, 0, "read only")
    assert json.loads(env.app.pushed_data("session.output.4")) == {
        "ack": 4, "code": 0, "data": "read only", "commits": [], "esr": {"wallet": None, "commits": []}}


def test_the_fences_file_keeps_the_esr_commit_name_and_stays_out_of_the_ledger(env, monkeypatch):
    ecld_state = env.ecld_state
    env.session._emit(WITH["envelope"]["ack"], *env.session._handle(WITH["envelope"]["ack"], "update"))
    # The node stages the committed objects by their CIDs.
    for key in ("a", "b"):
        blob = content(env.swift, f"state.{key}.enc")
        env.swift.put_file_content(BUCKET, f"state.{env.cids(blob)}.enc", "", __import__("io").BytesIO(blob))
    monkeypatch.setattr(ecld_state, "FENCE_POLL_SECONDS", 0.01)
    ledger = ecld_state.StateRegistry().fence(env.reg.block_number)
    fence = json.loads(F["fence"]["content"])
    assert ledger[0] == fence
    assert content(env.swift, F["fence"]["name"]) == F["fence"]["content"].encode("utf-8")
    assert names(env.swift, "esr.commit.") == sorted([F["fence"]["name"], "esr.commit.294581730c448338.1.json"])
    assert content(env.swift, "esr.authorizations.json") == F["authorizations"]["ledgerJson"].encode("utf-8")
    assert ecld_state.ledger_attestation()["commits"] == RECORDS
    assert env.reg.state(WALLET, KEYS["a"]) == (fence["cid"], 9, 0)


def test_an_ecld_test_run_stages_a_plain_tasks_commit_at_once(monkeypatch):
    m = load_modules(monkeypatch)
    local_api = m["local_api"]
    assert local_api._install_esr(default_caller=CALLER, persist=False)
    response = local_api.run_task(PLAIN_PAYLOAD, None, caller=CALLER)
    assert (response["task_code"], json.loads(response["result"])["data"]) == (0, "committed")
    swift = local_api._ESR["handles"]["swift"]
    assert len(names(swift, "esr.commit.")) == 1 and names(swift, "session.commit.") == []
    assert json.loads(content(swift, "esr.authorizations.json"))[0]["callerUsed"] == CALLER
