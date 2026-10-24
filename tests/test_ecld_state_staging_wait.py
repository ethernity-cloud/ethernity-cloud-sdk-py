"""A read of state committed by an earlier task waits for the node to stage
the object the chain names, for STATE_STAGE_WAIT_SECONDS, instead of failing
on its first look at the bucket (bloxberg testnet orders 2027-2030: the node
staged the object after the securelock had read). The chain and the bucket
are esr_local's in-memory stand-ins."""
import importlib
import io
import sys
import threading
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "ethernity_cloud_sdk_py/commands/pynithy/build/securelock/src"
CALLER = "0x" + "11" * 20
BUCKET = "ecld-local"


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
    monkeypatch.setattr(ecld_state, "STATE_STAGE_WAIT_SECONDS", 1.0)
    monkeypatch.setattr(ecld_state, "STATE_STAGE_POLL_SECONDS", 0.05)

    def relay():
        while pending:
            wired["registry"].apply_commit(*pending.pop(0))

    def new_task():
        ecld_state.configure(
            identity_priv=b"ecld-local-test-identity-key-0001",
            swift_stream_service=wired["swift"], bucket=BUCKET,
            contract_address="0xLOCAL", web3=wired["web3"], caller=CALLER)
        return ecld_state.StateRegistry()

    return ecld_state, wired, relay, new_task


def increment(state):
    return {**(state or {}), "n": (state or {}).get("n", 0) + 1}


def put(wired, name, data):
    wired["swift"].put_file_content(BUCKET, name, "", io.BytesIO(data))


def committed_and_relayed(wired, relay, new_task):
    """Commit the counter once and relay it; returns the committed object,
    leaving the key slot with an unrelayed blob, as a later commit does, and
    the object the chain names absent from the bucket."""
    new_task().commit("counter", increment)
    relay()
    ok, blob = wired["swift"].get_file_content_bytes(BUCKET, "state.counter.enc")
    assert ok
    put(wired, "state.counter.enc", b"unrelayed")
    return blob


def test_a_state_object_staged_after_the_read_began_is_returned(esr):
    ecld_state, wired, relay, new_task = esr
    blob = committed_and_relayed(wired, relay, new_task)
    threading.Timer(0.3, put, args=(wired, f"state.{ecld_state.unixfs_cid(blob)}.enc", blob)).start()
    assert new_task().get("counter") == {"n": 1}


def test_a_state_object_the_node_never_stages_fails_after_the_wait(esr):
    ecld_state, wired, relay, new_task = esr
    committed_and_relayed(wired, relay, new_task)
    with pytest.raises(RuntimeError, match="does not match the committed CID"):
        new_task().get("counter")


def test_a_read_with_nothing_in_the_bucket_says_so_after_the_wait(esr):
    ecld_state, wired, relay, new_task = esr
    committed_and_relayed(wired, relay, new_task)
    put(wired, "state.counter.enc", b"")
    with pytest.raises(RuntimeError, match="not staged within"):
        new_task().get("counter")
