"""ecld-info's ESR section reads an enclave's StateCommitted logs from the
chain head backwards in eth_getLogs ranges of at most LOG_RANGE_BLOCKS
blocks, inclusive, stopping at the range that completes the count asked for
and at COMMITS_LOOKBACK_BLOCKS below the head; the logs come back oldest
first. Checked against esr_local's registry as the chain."""
import importlib
import json
import sys
from pathlib import Path

import pytest

from ethernity_cloud_sdk_py.commands import info

SRC = Path(__file__).resolve().parents[1] / "ethernity_cloud_sdk_py/commands/pynithy/build/securelock/src"
CALLER = "0x" + "11" * 20


@pytest.fixture
def chain(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    for name in ("ecld_state", "esr_local"):
        sys.modules.pop(name, None)
    ecld_state = importlib.import_module("ecld_state")
    esr_local = importlib.import_module("esr_local")
    wired = esr_local.install(ecld_state, caller=CALLER, file_prefix=None)
    staged = []
    ecld_state.set_local_commit_apply(
        lambda enclave, key_hash, cid, expected_version, nonce=0:
            staged.append(key_hash.hex()[:16]))
    ecld_state.configure(
        identity_priv=b"ecld-local-test-identity-key-0001",
        swift_stream_service=wired["swift"], bucket="ecld-local",
        contract_address="0xLOCAL", web3=wired["web3"], caller=CALLER)
    registry = ecld_state.StateRegistry()

    def land(block):
        """One commit of the counter key, landed at `block`."""
        registry.commit("counter", lambda s: {"n": (s or {}).get("n", 0) + 1})
        wired["registry"].land_at(block)
        swift = wired["swift"]
        key16 = staged.pop(0)
        name = sorted(n for n in swift._store
                      if n.startswith("ecld-local/esr.commit.%s." % key16))[0]
        wired["registry"].relay(json.loads(swift._store.pop(name)))

    spans = []
    original = esr_local._StateCommittedEvent.get_logs

    def recording(self, argument_filters=None, from_block=None, to_block=None, **kwargs):
        spans.append((from_block, to_block))
        return original(self, argument_filters, from_block, to_block, **kwargs)
    monkeypatch.setattr(esr_local._StateCommittedEvent, "get_logs", recording)
    monkeypatch.setattr(info, "LOG_RANGE_BLOCKS", 7)
    monkeypatch.setattr(info, "COMMITS_LOOKBACK_BLOCKS", 100)

    for block in (10, 20, 25):
        land(block)
    w3 = wired["web3"]
    return w3, w3.eth.contract(), registry.wallet_address, spans


def blocks(logs):
    return [log.blockNumber for log in logs]


def test_the_walk_stops_at_the_range_that_completes_the_count(chain):
    w3, esr, wallet, spans = chain
    assert blocks(info._recent_commits(w3, esr, wallet, 2)) == [20, 25]
    assert spans == [(19, 25)]


def test_the_ranges_are_contiguous_and_at_most_the_range_width_inclusive(chain):
    w3, esr, wallet, spans = chain
    assert blocks(info._recent_commits(w3, esr, wallet, 3)) == [10, 20, 25]
    assert spans == [(19, 25), (12, 18), (5, 11)]
    assert all(hi - lo + 1 <= info.LOG_RANGE_BLOCKS for lo, hi in spans)


def test_the_walk_ends_at_the_lookback_below_the_head(chain, monkeypatch):
    w3, esr, wallet, spans = chain
    monkeypatch.setattr(info, "COMMITS_LOOKBACK_BLOCKS", 10)
    assert blocks(info._recent_commits(w3, esr, wallet, 5)) == [20, 25]
    assert spans == [(19, 25), (15, 18)]


def test_the_walk_ends_at_block_zero_when_the_count_is_not_reached(chain):
    w3, esr, wallet, spans = chain
    assert blocks(info._recent_commits(w3, esr, wallet, 5)) == [10, 20, 25]
    assert spans[-1] == (0, 4)
    assert len(spans) == 4


def test_another_enclaves_logs_are_not_read(chain):
    w3, esr, wallet, spans = chain
    assert info._recent_commits(w3, esr, "0x" + "22" * 20, 1) == []
