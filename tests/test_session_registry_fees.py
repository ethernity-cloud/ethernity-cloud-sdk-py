"""A SessionRegistry transaction carries the network's fees: a type-2
transaction priced from the latest base fee on an EIP-1559 chain (LitVM), the
network's gas price elsewhere (bloxberg). No network: web3 is replaced."""
import pytest
from web3 import Web3

from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks
from ethernity_cloud_sdk_py.commands.pynithy import session_registry

LITVM = BlockchainNetworks.LITVM_LITEFORGE
BLOXBERG = BlockchainNetworks.BLOXBERG_TESTNET
KEY = "0x" + "22" * 32


class _Block:
    def __init__(self, base_fee):
        self.baseFeePerGas = base_fee


class _Account:
    address = "0x" + "33" * 20


class _Signed:
    raw_transaction = b"\x02signed"


class _Eth:
    def __init__(self, base_fee):
        self._base_fee = base_fee
        self.account = type("A", (), {
            "from_key": staticmethod(lambda key: _Account()),
            "sign_transaction": staticmethod(lambda txn, private_key: _Signed()),
        })()

    def get_block(self, tag):
        assert tag == "latest"
        return _Block(self._base_fee)

    def get_transaction_count(self, address, tag):
        return 7

    def send_raw_transaction(self, raw):
        return b"\xaa" * 32

    def wait_for_transaction_receipt(self, txh, timeout=None):
        return type("R", (), {"status": 1})()


class _Web3:
    to_wei = staticmethod(Web3.to_wei)

    def __init__(self, base_fee=0):
        self.eth = _Eth(base_fee)


class _Fn:
    """A contract function: its simulation passes, its estimate is 100,000
    gas, and the transaction it builds is kept."""
    built = None

    def call(self, tx):
        return None

    def estimate_gas(self, tx):
        return 100_000

    def build_transaction(self, tx):
        _Fn.built = tx
        return tx


def test_an_eip1559_network_prices_from_the_base_fee():
    fees = session_registry.fee_fields(_Web3(base_fee=12_749_000), LITVM)
    assert fees == {"type": 2, "maxFeePerGas": int(12_749_000 * 1.1), "maxPriorityFeePerGas": 0}


def test_a_base_fee_above_the_networks_cap_is_refused():
    with pytest.raises(SystemExit, match="above the network's"):
        session_registry.fee_fields(_Web3(base_fee=Web3.to_wei(3, "gwei")), LITVM)


def test_a_legacy_network_takes_its_gas_price():
    assert session_registry.fee_fields(_Web3(), BLOXBERG) == {"gasPrice": Web3.to_wei(0.002, "gwei")}


def test_send_builds_the_transaction_with_the_networks_fees_and_chain():
    session_registry._send(_Web3(base_fee=12_749_000), LITVM, KEY, _Fn(), label="x")
    assert _Fn.built["chainId"] == 4441
    assert _Fn.built["gas"] == 130_000
    assert _Fn.built["nonce"] == 7
    assert _Fn.built["type"] == 2 and "gasPrice" not in _Fn.built
    assert _Fn.built["maxFeePerGas"] == int(12_749_000 * 1.1)


def test_send_on_bloxberg_is_a_legacy_transaction():
    session_registry._send(_Web3(), BLOXBERG, KEY, _Fn(), label="x")
    assert _Fn.built["chainId"] == 8995
    assert _Fn.built["gasPrice"] == Web3.to_wei(0.002, "gwei") and "maxFeePerGas" not in _Fn.built
