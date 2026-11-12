"""esr_local's registry as the chain the securelock reads back: a landed
commit is one StateCommitted log at the chain head with the contract's seq,
version and nonce; getEntriesFrom lists each (enclave, key) once in
first-commit order with its current state; land_at places later landings on
later blocks, so a read bounded by to_block sees the state as of that block;
w3.eth.get_logs answers eth_getLogs with the ABI-encoded raw logs that
contract.events.StateCommitted().process_log decodes, with a bound that is
absent or "latest" at the chain head; relay() applies a staged
esr.commit.*.json as commitFor does and refuses a relay nonce the registry
has moved past; a registry file without logs is not loaded."""
import importlib
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "ethernity_cloud_sdk_py/commands/pynithy/build/securelock/src"
CALLER = "0x" + "11" * 20
ENCLAVE = "0xabCDeF0123456789AbcdEf0123456789aBCDEF01"
KEY = b"\x01" * 32
# keccak256("StateCommitted(address,bytes32,string,uint256,uint256,uint256)")
TOPIC0 = "0xd81b6507fef29869f73ffa29c22df83613804f04c676f99d26f14ef602b2edbd"


@pytest.fixture
def esr(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    for name in ("ecld_state", "esr_local"):
        sys.modules.pop(name, None)
    ecld_state = importlib.import_module("ecld_state")
    esr_local = importlib.import_module("esr_local")
    wired = esr_local.install(ecld_state, caller=CALLER, file_prefix=None)
    # The node's relay: the staged authorizations, applied in staging order.
    staged = []
    ecld_state.set_local_commit_apply(
        lambda enclave, key_hash, cid, expected_version, nonce=0:
            staged.append(key_hash.hex()[:16]))

    def relay():
        swift = wired["swift"]
        while staged:
            key16 = staged.pop(0)
            names = sorted(n for n in swift._store
                           if n.startswith("ecld-local/esr.commit.%s." % key16))
            name = names[0]
            auth = json.loads(swift._store.pop(name))
            wired["registry"].relay(auth)

    def new_task():
        ecld_state.configure(
            identity_priv=b"ecld-local-test-identity-key-0001",
            swift_stream_service=wired["swift"], bucket="ecld-local",
            contract_address="0xLOCAL", web3=wired["web3"], caller=CALLER)
        return ecld_state.StateRegistry()

    return ecld_state, wired, relay, new_task


def increment(state):
    return {**(state or {}), "n": (state or {}).get("n", 0) + 1}


def test_a_landed_commit_is_one_log_at_the_head_block(esr):
    ecld_state, wired, relay, new_task = esr
    registry = new_task()
    registry.commit("counter", increment)
    relay()
    contract = wired["web3"].eth.contract()
    logs = contract.events.StateCommitted.get_logs()
    assert len(logs) == 1
    log = logs[0]
    cid, version, _ = wired["registry"].state(registry.wallet_address, ecld_state._key_hash("counter"))
    assert log.args.enclave == registry.wallet_address
    assert log.args.key == ecld_state._key_hash("counter")
    assert (log.args.cid, log.args.version, log.args.seq, log.args.nonce) == (cid, 1, 1, 1)
    assert log.blockNumber == wired["web3"].eth.block_number == 1
    assert log.logIndex == 0 and len(log.transactionHash) == 32
    assert contract.functions.commitSeq().call() == 1


def test_get_entries_from_lists_each_key_once_in_first_commit_order(esr):
    ecld_state, wired, relay, new_task = esr
    registry = new_task()
    registry.commit("a", increment)
    registry.commit("b", increment)
    registry.commit("a", increment)
    relay()
    contract = wired["web3"].eth.contract()
    enclaves, keys, cids, versions, updated_ats, total = contract.functions.getEntriesFrom(0, 10).call()
    assert total == 2 and contract.functions.entryCount().call() == 2
    assert enclaves == [registry.wallet_address] * 2
    assert keys == [ecld_state._key_hash("a"), ecld_state._key_hash("b")]
    assert versions == [2, 1]
    assert cids == [wired["registry"].state(registry.wallet_address, k)[0] for k in keys]
    assert updated_ats == [0, 0]
    assert contract.functions.entryAt(1).call() == (registry.wallet_address, keys[1])
    assert contract.functions.getEntriesFrom(1, 1).call()[1] == [keys[1]]
    assert contract.functions.getEntriesFrom(2, 1).call() == ([], [], [], [], [], 2)
    assert contract.functions.getEntriesFrom(0, 0).call() == ([], [], [], [], [], 2)
    assert [l.args.seq for l in contract.events.StateCommitted.get_logs()] == [1, 2, 3]


def test_land_at_places_later_landings_on_later_blocks(esr):
    ecld_state, wired, relay, new_task = esr
    registry = new_task()
    key = ecld_state._key_hash("counter")
    registry.commit("counter", increment)
    wired["registry"].land_at(10)
    relay()
    registry.commit("counter", increment)
    wired["registry"].land_at(20)
    relay()
    registry.commit("other", increment)
    relay()
    contract = wired["web3"].eth.contract()
    event = contract.events.StateCommitted
    bound = event.get_logs(argument_filters={"enclave": registry.wallet_address, "key": key},
                           from_block=0, to_block=15)
    assert [(l.blockNumber, l.args.version) for l in bound] == [(10, 1)]
    at_20 = event.get_logs(argument_filters={"key": key}, fromBlock=11, toBlock=20)
    assert [(l.blockNumber, l.args.version) for l in at_20] == [(20, 2)]
    assert [l.logIndex for l in event.get_logs(from_block=20)] == [0, 1]
    assert wired["web3"].eth.block_number == 20
    with pytest.raises(ValueError):
        wired["registry"].land_at(19)


def test_relay_applies_a_staged_authorization_once(esr):
    ecld_state, wired, relay, new_task = esr
    registry = new_task()
    registry.commit("counter", increment)
    key = ecld_state._key_hash("counter")
    name = next(n for n in wired["swift"]._store if n.startswith("ecld-local/esr.commit."))
    auth = json.loads(wired["swift"]._store[name])
    assert name.endswith(".0.json") and auth["relayNonce"] == 0
    wired["registry"].relay(auth)
    assert wired["registry"].relay_nonce(registry.wallet_address, key) == 1
    assert wired["registry"].state(registry.wallet_address, key)[1] == 1
    with pytest.raises(RuntimeError, match="RelayNonceMismatch"):
        wired["registry"].relay(auth)
    assert wired["registry"].state(registry.wallet_address, key)[1] == 1
    assert len(wired["registry"].logs()) == 1


def test_eth_get_logs_answers_the_web3_filter_with_raw_logs_that_process_log_decodes(esr):
    from web3 import Web3
    ecld_state, wired, relay, new_task = esr
    esr_local = sys.modules["esr_local"]
    address = Web3.to_checksum_address("0x" + "ab" * 20)
    reg = esr_local.MemRegistry(address=address)
    reg.land_at(5)
    reg.apply_commit(ENCLAVE, KEY, "bafy1", 0)
    reg.land_at(9)
    reg.apply_commit(ENCLAVE, KEY, "bafy2", 1)
    reg.apply_commit(CALLER, b"\x02" * 32, "bafy3", 0)
    w3 = esr_local.MemWeb3(reg)
    # The filter contract.events.StateCommitted.get_logs(argument_filters=
    # {"enclave": ENCLAVE, "key": KEY}, to_block=8) sends to eth_getLogs.
    raw = w3.eth.get_logs({
        "topics": [TOPIC0, "0x" + "00" * 12 + ENCLAVE[2:].lower(), "0x" + KEY.hex()],
        "address": [address], "fromBlock": 0, "toBlock": 8})
    assert [(l.blockNumber, l.logIndex) for l in raw] == [(5, 0)]
    log = raw[0]
    assert log.address == address and log.removed is False and log.transactionIndex == 0
    assert [t.hex() for t in log.topics] == [TOPIC0[2:], "00" * 12 + ENCLAVE[2:].lower(), KEY.hex()]
    assert len(log.blockHash) == 32 and len(log.transactionHash) == 32
    abi = [e for e in json.loads((SRC / "esr.abi").read_text()) if e.get("name") == "StateCommitted"]
    real = Web3().eth.contract(address=address, abi=abi).events.StateCommitted()
    decoded = w3.eth.contract().events.StateCommitted().process_log(log)
    assert dict(real.process_log(dict(log))) == {
        **{k: v for k, v in decoded.items() if k != "args"}, "args": dict(decoded.args)}
    assert decoded.args.enclave == ENCLAVE and decoded.args.key == KEY
    assert (decoded.args.cid, decoded.args.version, decoded.args.seq, decoded.args.nonce) == ("bafy1", 1, 1, 1)
    # Topic alternatives, an open position, and the address check.
    both = w3.eth.get_logs({"topics": [TOPIC0, None, ["0x" + KEY.hex(), "0x" + "02" * 32]], "fromBlock": 0})
    assert [l.data[-32:].rstrip(b"\0") for l in both] == [b"bafy1", b"bafy2", b"bafy3"]
    assert w3.eth.get_logs({"topics": [TOPIC0], "fromBlock": 0, "address": CALLER}) == []
    assert w3.eth.get_logs({"topics": [TOPIC0], "fromBlock": 0, "address": address.lower()}) == both
    assert w3.eth.get_logs({"fromBlock": 0}) == both


def test_a_block_bound_absent_or_latest_is_the_chain_head(esr):
    ecld_state, wired, relay, new_task = esr
    esr_local = sys.modules["esr_local"]
    reg = esr_local.MemRegistry()
    reg.apply_commit(ENCLAVE, KEY, "bafy1", 0)
    reg.land_at(4)
    reg.apply_commit(ENCLAVE, KEY, "bafy2", 1)
    w3 = esr_local.MemWeb3(reg)
    event = w3.eth.contract().events.StateCommitted
    assert [event().process_log(l).args.cid for l in w3.eth.get_logs({})] == ["bafy2"]
    assert [l.args.cid for l in event.get_logs()] == ["bafy2"]
    assert [l.args.cid for l in event.get_logs(from_block="latest")] == ["bafy2"]
    assert [l.args.cid for l in event.get_logs(from_block="earliest")] == ["bafy1", "bafy2"]
    assert [l.args.cid for l in event.get_logs(from_block="earliest", to_block="latest")] == ["bafy1", "bafy2"]
    assert [l.args.cid for l in event.get_logs(from_block="earliest", to_block="earliest")] == []
    assert [l.args.cid for l in event.get_logs(from_block=1, to_block=3)] == ["bafy1"]
    assert [l.args.cid for l in reg.logs(from_block=2)] == ["bafy2"]


def test_the_registry_file_keeps_the_logs_the_entries_and_the_head(esr, tmp_path):
    ecld_state, wired, relay, new_task = esr
    esr_local = sys.modules["esr_local"]
    path = str(tmp_path / "registry.json")
    persisted = esr_local.MemRegistry(file=path)
    persisted.land_at(7)
    persisted.apply_commit(ENCLAVE, KEY, "bafy1", 0)
    persisted.apply_commit(ENCLAVE, KEY, "bafy2", 1)
    reloaded = esr_local.MemRegistry(file=path)
    assert reloaded.block_number == 7 and reloaded.commit_seq() == 2
    assert reloaded.entries_from(0, 10) == ([ENCLAVE], [KEY], ["bafy2"], [2], [0], 1)
    assert [(l.args.cid, l.blockNumber) for l in reloaded.logs(key_hash=KEY)] == [("bafy1", 7), ("bafy2", 7)]
    assert reloaded.relay_nonce(ENCLAVE, KEY) == 2


def test_a_registry_file_without_logs_is_not_loaded(esr, tmp_path):
    ecld_state, wired, relay, new_task = esr
    esr_local = sys.modules["esr_local"]
    path = tmp_path / "registry.json"
    key = "%s|%s" % (ENCLAVE.lower(), KEY.hex())
    path.write_text(json.dumps({"entries": {key: {"cid": "bafy9", "version": 3, "updatedAt": 0, "nonce": 3}},
                                "nonce": {key: 3}}))
    reg = esr_local.MemRegistry(file=str(path))
    assert reg.entry_count() == 0 and reg.commit_seq() == 0 and reg.block_number == 1
    assert reg.state(ENCLAVE, KEY) == ("", 0, 0)
    assert reg.relay_nonce(ENCLAVE, KEY) == 0 and reg.logs() == []
    assert reg.entries_from(0, 10) == ([], [], [], [], [], 0)
    reg.apply_commit(ENCLAVE, KEY, "bafy1", 0)
    assert json.loads(path.read_text())["seq"] == 1
    assert esr_local.MemRegistry(file=str(path)).state(ENCLAVE, KEY) == ("bafy1", 1, 0)
