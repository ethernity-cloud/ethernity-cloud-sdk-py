"""The securelock's state registry while the node has not yet relayed a commit
to the chain: a read in the same task sees the commit, a second commit builds
on it, and a later task reads the committed object even though the bucket's
state.<key>.enc holds a newer blob. The chain and the bucket are esr_local's
in-memory stand-ins; commits reach the registry only when relay() runs, as
they reach the chain only when the node relays them."""
import importlib
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "ethernity_cloud_sdk_py/commands/pynithy/build/securelock/src"
CALLER = "0x" + "11" * 20


@pytest.fixture
def esr(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    for name in ("ecld_state", "esr_local"):
        sys.modules.pop(name, None)
    ecld_state = importlib.import_module("ecld_state")
    esr_local = importlib.import_module("esr_local")
    wired = esr_local.install(ecld_state, caller=CALLER, file_prefix=None)
    pending = []
    ecld_state.set_local_commit_apply(
        lambda enclave, key_hash, cid, expected_version, nonce=0:
            pending.append((enclave, key_hash, cid, expected_version, nonce)))

    def relay():
        while pending:
            wired["registry"].apply_commit(*pending.pop(0))

    def new_task():
        ecld_state.configure(
            identity_priv=b"ecld-local-test-identity-key-0001",
            swift_stream_service=wired["swift"], bucket="ecld-local",
            contract_address="0xLOCAL", web3=wired["web3"], caller=CALLER)
        return ecld_state.StateRegistry()

    return ecld_state, wired, relay, new_task


def increment(state):
    return {**(state or {}), "n": (state or {}).get("n", 0) + 1}


def test_a_read_after_a_commit_in_the_same_task_returns_the_commit(esr):
    _, _, _, new_task = esr
    registry = new_task()
    registry.commit("counter", increment)
    assert registry.get("counter") == {"n": 1}


def test_a_second_commit_in_the_same_task_builds_on_the_first(esr):
    _, _, relay, new_task = esr
    registry = new_task()
    registry.commit("counter", increment)
    registry.commit("counter", increment)
    relay()
    assert new_task().get("counter") == {"n": 2}


def test_a_later_task_commits_and_reads_while_its_commit_is_unrelayed(esr):
    """Bloxberg testnet orders 2006 and 2007: the commit overwrote
    state.<key>.enc, and the read that followed checked it against the
    chain's previous CID."""
    _, _, relay, new_task = esr
    new_task().commit("counter", increment)
    relay()
    registry = new_task()
    registry.commit("counter", increment)
    assert registry.get("counter") == {"n": 2}
    relay()
    assert new_task().get("counter") == {"n": 2}


def test_the_object_named_by_the_committed_cid_is_read_before_the_key_slot(esr):
    ecld_state, wired, relay, new_task = esr
    new_task().commit("counter", increment)
    relay()
    cid, _, _ = wired["registry"].state(new_task().wallet_address, ecld_state._key_hash("counter"))
    committed = wired["swift"].get_file_content_bytes("ecld-local", "state.counter.enc")[1]
    # The node stages the committed object by its CID; the key slot then
    # holds a blob of a commit that never reached the chain.
    wired["swift"].put_file_content("ecld-local", f"state.{cid}.enc", "", __import__("io").BytesIO(committed))
    wired["swift"].put_file_content("ecld-local", "state.counter.enc", "", __import__("io").BytesIO(b"unrelayed"))
    assert new_task().get("counter") == {"n": 1}


def test_a_state_object_above_one_block_commits_under_its_unixfs_root_and_reads_back(esr):
    """A state object is stored as IPFS blocks of 256 KiB; above one block its
    CID is the root of the UnixFS tree over them, computed in the enclave as
    the node's Kubo computes it, so the node stores the object under the
    committed CID."""
    ecld_state, wired, relay, new_task = esr
    payload = {"payload": "x" * 300000}
    new_task().commit("big", lambda state: payload)
    relay()
    cid, version, _ = wired["registry"].state(new_task().wallet_address, ecld_state._key_hash("big"))
    assert version == 1 and cid.startswith("bafybei")
    stored = wired["swift"].get_file_content_bytes("ecld-local", "state.big.enc")[1]
    assert len(stored) > ecld_state.BLOCK_SIZE
    assert ecld_state.unixfs_cid(stored) == cid
    assert new_task().get("big") == payload


def test_a_blob_that_matches_no_committed_cid_is_refused(esr):
    _, wired, relay, new_task = esr
    new_task().commit("counter", increment)
    relay()
    wired["swift"].put_file_content("ecld-local", "state.counter.enc", "", __import__("io").BytesIO(b"tampered"))
    with pytest.raises(RuntimeError, match="does not match the committed CID"):
        new_task().get("counter")
