"""The ceiling of the fee per gas on an EIP-1559 network: Polygon mainnet's
default of 500 gwei, ECLD_MAX_FEE_PER_GAS over a network's default, the
refusal at the image registry's and the SessionRegistry's sends, and
ecld-publish stopping beside its gas balance check before a session is
posted, an image pinned or a transaction sent. No network: web3 is replaced.
image_registry.py and both publish.py read the .config.json of the CWD at
import, so they are imported from a directory holding one."""
import importlib
import json
import types

import pytest
import requests
from web3 import Web3

from ethernity_cloud_sdk_py.commands.config import Config
from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks
from ethernity_cloud_sdk_py.commands.pynithy import session_registry
from ethernity_cloud_sdk_py.commands.spinner import Spinner

POLYGON = BlockchainNetworks.POLYGON_MAINNET
LITVM = BlockchainNetworks.LITVM_LITEFORGE
BLOXBERG = BlockchainNetworks.BLOXBERG_TESTNET
VARIABLE = "ECLD_MAX_FEE_PER_GAS"
KEY = "0x" + "11" * 32
GAS_LIMIT = 1_200_000
STEP = "Checking the fee per gas"

REFUSAL_AT_500 = ("Polygon Mainnet: a base fee of 426 gwei puts the fee per gas at 500.6 gwei, "
                  "above the network's default ceiling of 500 gwei "
                  "(ECLD_MAX_FEE_PER_GAS sets another, in whole gwei)")
REFUSAL_AT_600 = ("Polygon Mainnet: a base fee of 517 gwei puts the fee per gas at 600.7 gwei, "
                  "above the ceiling of 600 gwei that ECLD_MAX_FEE_PER_GAS sets")
REFUSAL_AT_100 = ("Polygon Mainnet: a base fee of 62 gwei puts the fee per gas at 100.2 gwei, "
                  "above the ceiling of 100 gwei that ECLD_MAX_FEE_PER_GAS sets")
LITVM_REFUSAL = ("LitVM LiteForge: a base fee of 3 gwei puts the fee per gas at 3.3 gwei, "
                 "above the network's default ceiling of 2 gwei "
                 "(ECLD_MAX_FEE_PER_GAS sets another, in whole gwei)")

# The highest base fee Polygon mainnet's default ceiling admits, in wei:
# int(425_454_545_455 * 1.1) + 32 gwei is 500 gwei to the wei.
BASE_FEE_AT_THE_CEILING = 425_454_545_455
REFUSAL_ONE_WEI_ABOVE = ("Polygon Mainnet: a base fee of 425.454545456 gwei puts the fee per gas at "
                         "500.000000001 gwei, above the network's default ceiling of 500 gwei "
                         "(ECLD_MAX_FEE_PER_GAS sets another, in whole gwei)")


def gwei(amount):
    return Web3.to_wei(amount, "gwei")


def polygon_fees(base_fee_gwei):
    """The fee fields of a Polygon mainnet transaction at that base fee: the
    base fee plus a tenth, plus the network's priority fee of 32 gwei."""
    return {"type": 2, "maxFeePerGas": int(gwei(base_fee_gwei) * 1.1) + gwei(32),
            "maxPriorityFeePerGas": gwei(32)}


def variable_refusal(value):
    return (f"ECLD_MAX_FEE_PER_GAS is {value!r}, not a positive whole number of gwei in up to 30 digits alone "
            f"(the ceiling of the fee per gas); unset it for the network's default")


@pytest.fixture(autouse=True)
def _no_ceiling_variable(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)


class _Block:
    def __init__(self, base_fee):
        self.baseFeePerGas = base_fee


class _Signed:
    """A signed transaction: the hash a registration prints and the raw bytes
    it sends."""
    hash = b"\x5a" * 32
    raw_transaction = b"\x02signed"


class _Account:
    def __init__(self, calls):
        self._calls = calls

    def sign_transaction(self, txn, private_key):
        self._calls.append("sign_transaction")
        return _Signed()


class _Eth:
    """The chain a send reads: a latest block at `base_fee`, a pending nonce
    of 7 and a balance of 5 of the gas token. A transaction sent to it is
    mined with status 1. `calls` lists the methods called, in order."""

    def __init__(self, base_fee):
        self._base_fee = base_fee
        self.calls = []
        self.account = _Account(self.calls)

    def get_block(self, tag):
        assert tag == "latest"
        self.calls.append("get_block")
        return _Block(self._base_fee)

    def get_transaction_count(self, address, tag):
        assert tag == "pending"
        self.calls.append("get_transaction_count")
        return 7

    def get_balance(self, address):
        self.calls.append("get_balance")
        return Web3.to_wei(5, "ether")

    def send_raw_transaction(self, raw):
        self.calls.append("send_raw_transaction")
        return b"\xaa" * 32

    def wait_for_transaction_receipt(self, tx_hash):
        self.calls.append("wait_for_transaction_receipt")
        return types.SimpleNamespace(status=1)


class _Web3:
    to_wei = staticmethod(Web3.to_wei)

    def __init__(self, base_fee=None):
        self.eth = _Eth(base_fee)


class _Fn:
    """A contract function whose simulation passes; `built` lists the options
    of the transactions built from it."""

    def __init__(self):
        self.built = []

    def call(self, tx):
        return None

    def build_transaction(self, options):
        self.built.append(options)
        return options


def _config(tmp_path, network):
    (tmp_path / ".config.json").write_text(json.dumps({
        "PROJECT_NAME": "demo", "BLOCKCHAIN_NETWORK": network.name, "DAPP_TYPE": "Pynithy",
        "VERSION": 1, "IPFS_HASH": ""}), encoding="utf-8")
    cfg = Config(tmp_path / ".config.json")
    cfg.load()
    return cfg


def _image_registry(monkeypatch, tmp_path, network, w3):
    """An ImageRegistry of `network` over a .config.json in tmp_path, the CWD,
    whose chain is `w3` and whose wallet is KEY's. Its contract is removed:
    no test reaches it."""
    cfg = _config(tmp_path, network)
    monkeypatch.chdir(tmp_path)
    module = importlib.import_module("ethernity_cloud_sdk_py.commands.pynithy.run.image_registry")
    monkeypatch.setattr(module, "config", cfg)
    registry = module.ImageRegistry()
    registry.provider = w3
    registry.image_registry_contract = None
    registry.set_private_key(KEY)
    return registry


def _image_registry_fees(monkeypatch, tmp_path, network, w3):
    """The fee side of the options an image registry transaction is built
    with (ImageRegistry._transaction_options)."""
    options = _image_registry(monkeypatch, tmp_path, network, w3)._transaction_options(GAS_LIMIT)
    return {name: value for name, value in options.items() if name not in ("nonce", "chainId", "from", "gas")}


def _session_registry_fees(monkeypatch, tmp_path, network, w3):
    """The fee fields of a SessionRegistry transaction (session_registry.fee_fields)."""
    return session_registry.fee_fields(w3, network)


# The two sends, and what each raises for a refusal: the image registry's an
# Exception its callers print, the SessionRegistry's the exit of its module.
SENDS = [pytest.param(_image_registry_fees, id="image registry"),
         pytest.param(_session_registry_fees, id="session registry")]
REFUSED_AS = {_image_registry_fees: Exception, _session_registry_fees: SystemExit}


def test_polygon_mainnets_default_ceiling_is_500_gwei():
    assert POLYGON.is_eip1559
    assert POLYGON.max_fee_per_gas == 500
    assert POLYGON.max_priority_fee_per_gas == 32


def test_the_default_ceilings_and_priority_fees_of_the_eip1559_networks():
    assert {member.name: (member.max_fee_per_gas, member.max_priority_fee_per_gas)
            for member in BlockchainNetworks if member.is_eip1559} == {
        "POLYGON_MAINNET": (500, 32), "POLYGON_AMOY": (500, 32), "POLYGON_AMOY_UNSAFE": (500, 32),
        "IOTEX_TESTNET": (1500, 1), "ETHEREUM_SEPOLIA": (15, 1), "ETHEREUM_SEPOLIA_UNSAFE": (15, 1),
        "LITVM_LITEFORGE": (2, 0), "LITVM_LITEFORGE_UNSAFE": (2, 0), "POLYGON_AMOY_POX10": (500, 32)}


@pytest.mark.parametrize("send", SENDS)
def test_at_the_default_a_base_fee_of_425_gwei_is_sent_on_polygon_mainnet(monkeypatch, tmp_path, send):
    assert send(monkeypatch, tmp_path, POLYGON, _Web3(gwei(425))) == polygon_fees(425)
    assert polygon_fees(425)["maxFeePerGas"] == 499_500_000_000


@pytest.mark.parametrize("send", SENDS)
def test_at_the_default_a_base_fee_of_426_gwei_is_refused_on_polygon_mainnet(monkeypatch, tmp_path, send):
    """After one read of the block."""
    w3 = _Web3(gwei(426))
    with pytest.raises(REFUSED_AS[send]) as refusal:
        send(monkeypatch, tmp_path, POLYGON, w3)
    assert str(refusal.value) == REFUSAL_AT_500
    assert w3.eth.calls.count("get_block") == 1


@pytest.mark.parametrize("send", SENDS)
def test_a_fee_equal_to_the_ceiling_is_sent_and_one_wei_above_it_is_refused(monkeypatch, tmp_path, send):
    assert send(monkeypatch, tmp_path, POLYGON, _Web3(BASE_FEE_AT_THE_CEILING))["maxFeePerGas"] == gwei(500)
    with pytest.raises(REFUSED_AS[send]) as refusal:
        send(monkeypatch, tmp_path, POLYGON, _Web3(BASE_FEE_AT_THE_CEILING + 1))
    assert str(refusal.value) == REFUSAL_ONE_WEI_ABOVE


@pytest.mark.parametrize("send", SENDS)
@pytest.mark.parametrize("value, sent, refused, message", [
    pytest.param("600", 516, 517, REFUSAL_AT_600, id="600"),
    pytest.param("100", 61, 62, REFUSAL_AT_100, id="100"),
])
def test_the_variable_is_the_ceiling_in_whole_gwei(monkeypatch, tmp_path, send, value, sent, refused, message):
    monkeypatch.setenv(VARIABLE, value)
    assert send(monkeypatch, tmp_path, POLYGON, _Web3(gwei(sent))) == polygon_fees(sent)
    with pytest.raises(REFUSED_AS[send]) as refusal:
        send(monkeypatch, tmp_path, POLYGON, _Web3(gwei(refused)))
    assert str(refusal.value) == message


@pytest.mark.parametrize("send", SENDS)
@pytest.mark.parametrize("network", [member for member in BlockchainNetworks if member.is_eip1559],
                         ids=lambda member: member.name)
def test_the_variable_is_the_ceiling_on_every_eip1559_network(monkeypatch, tmp_path, send, network):
    """A base fee equal to the network's default ceiling, which the default
    refuses, is sent under a ceiling of twice that plus the priority fee."""
    base_fee = gwei(network.max_fee_per_gas)
    priority = gwei(network.max_priority_fee_per_gas)
    with pytest.raises(REFUSED_AS[send]):
        send(monkeypatch, tmp_path, network, _Web3(base_fee))
    monkeypatch.setenv(VARIABLE, str(2 * network.max_fee_per_gas + network.max_priority_fee_per_gas))
    assert send(monkeypatch, tmp_path, network, _Web3(base_fee)) == {
        "type": 2, "maxFeePerGas": int(base_fee * 1.1) + priority, "maxPriorityFeePerGas": priority}


@pytest.mark.parametrize("send", SENDS)
def test_an_empty_variable_leaves_the_networks_default(monkeypatch, tmp_path, send):
    monkeypatch.setenv(VARIABLE, "")
    assert send(monkeypatch, tmp_path, POLYGON, _Web3(gwei(425))) == polygon_fees(425)
    with pytest.raises(REFUSED_AS[send]) as refusal:
        send(monkeypatch, tmp_path, POLYGON, _Web3(gwei(426)))
    assert str(refusal.value) == REFUSAL_AT_500


@pytest.mark.parametrize("send", SENDS)
@pytest.mark.parametrize("value", [
    "0", "-500", "+500", "1.5", "5e2", "0x1f4", "500 gwei", "abc",
    pytest.param(" 600 ", id="600 in spaces"), pytest.param("   ", id="spaces")])
def test_a_variable_that_is_not_a_positive_whole_number_in_digits_is_refused(monkeypatch, tmp_path, send, value):
    """With the variable and its value named, at a base fee the network's
    default admits, and before the block is read."""
    monkeypatch.setenv(VARIABLE, value)
    w3 = _Web3(gwei(100))
    with pytest.raises(REFUSED_AS[send]) as refusal:
        send(monkeypatch, tmp_path, POLYGON, w3)
    assert str(refusal.value) == variable_refusal(value)
    assert "get_block" not in w3.eth.calls


@pytest.mark.parametrize("send", SENDS)
def test_a_variable_of_30_digits_is_the_ceiling(monkeypatch, tmp_path, send):
    """A base fee of 426 gwei, which Polygon mainnet's default refuses, is sent."""
    monkeypatch.setenv(VARIABLE, "9" * 30)
    assert send(monkeypatch, tmp_path, POLYGON, _Web3(gwei(426))) == polygon_fees(426)


@pytest.mark.parametrize("send", SENDS)
@pytest.mark.parametrize("digits", [31, 5000])
def test_a_variable_of_more_than_30_digits_is_refused(monkeypatch, tmp_path, send, digits):
    """As a value that is not a positive whole number is, before the block is
    read; 5000 digits are more than int() converts (4300 by default,
    sys.get_int_max_str_digits)."""
    value = "9" * digits
    monkeypatch.setenv(VARIABLE, value)
    w3 = _Web3(gwei(100))
    with pytest.raises(REFUSED_AS[send]) as refusal:
        send(monkeypatch, tmp_path, POLYGON, w3)
    assert str(refusal.value) == variable_refusal(value)
    assert "get_block" not in w3.eth.calls


@pytest.mark.parametrize("send", SENDS)
@pytest.mark.parametrize("value", [pytest.param(None, id="unset"), "1", "abc"])
def test_a_network_of_legacy_transactions_takes_its_gas_price_whatever_the_variable(
        monkeypatch, tmp_path, send, value):
    if value is not None:
        monkeypatch.setenv(VARIABLE, value)
    w3 = _Web3()
    assert send(monkeypatch, tmp_path, BLOXBERG, w3) == {"gasPrice": Web3.to_wei(0.002, "gwei")}
    assert "get_block" not in w3.eth.calls


def test_an_addimage_transaction_is_not_built_above_the_ceiling(monkeypatch, tmp_path, capsys):
    """Polygon mainnet's registry takes an image by addImage:
    build_transaction_add_image prints the refusal and gives no transaction,
    with nothing signed."""
    w3 = _Web3(gwei(426))
    registry = _image_registry(monkeypatch, tmp_path, POLYGON, w3)
    built = registry.build_transaction_add_image("certificate", "QmImage", "demo", "v3", "QmCompose", "session", 0)
    assert built is False
    assert f"\tFailed to prepare and sign transaction: {REFUSAL_AT_500}\n" in capsys.readouterr().out
    assert w3.eth.calls == ["get_transaction_count", "get_block"]


def test_a_registry_send_raises_the_refusal_before_a_transaction_is_built(monkeypatch, tmp_path):
    """registerImage and setImageCert go through ImageRegistry._send: the
    simulation passes, then the refusal is raised with nothing built or signed."""
    w3 = _Web3(gwei(3))
    registry = _image_registry(monkeypatch, tmp_path, LITVM, w3)
    function = _Fn()
    with pytest.raises(Exception) as refusal:
        registry._send(function, GAS_LIMIT, "Registering demo v3")
    assert str(refusal.value) == LITVM_REFUSAL
    assert function.built == []
    assert w3.eth.calls == ["get_transaction_count", "get_block"]


def _addimage_registration(monkeypatch, tmp_path, network, w3):
    """An ImageRegistry of `network` over `w3`, set for
    register_securelock_image as the publish script calls it on a registry
    that takes an image by addImage: .config.json names an image and its
    compose, the registry holds no image under the name, and the 5 s wait
    before each attempt is recorded, not slept. Returns the registry, the
    addImage function and the waits."""
    registry = _image_registry(monkeypatch, tmp_path, network, w3)
    module = importlib.import_module("ethernity_cloud_sdk_py.commands.pynithy.run.image_registry")
    module.config.write("IPFS_HASH", "QmImage")
    module.config.write("IPFS_DOCKER_COMPOSE_HASH", "QmCompose")
    function, waits = _Fn(), []
    registry.image_registry_contract = types.SimpleNamespace(
        functions=types.SimpleNamespace(addImage=lambda *arguments: function))
    monkeypatch.setattr(registry, "_get_latest_image_version_public_key", lambda name, version: ("", "", ""))
    monkeypatch.setattr(module, "time", types.SimpleNamespace(sleep=waits.append))
    return registry, function, waits


def test_a_refusal_at_the_addimage_registration_is_raised_at_once(monkeypatch, tmp_path):
    """Polygon mainnet's registration: the refusal is raised after one wait
    and one read of the block, with no transaction built or signed and no
    second attempt."""
    w3 = _Web3(gwei(426))
    registry, function, waits = _addimage_registration(monkeypatch, tmp_path, POLYGON, w3)
    with pytest.raises(Exception) as refusal:
        registry.register_securelock_image("certificate")
    assert str(refusal.value) == REFUSAL_AT_500
    assert function.built == []
    assert w3.eth.calls == ["get_block"]
    assert waits == [5]


def test_the_addimage_registration_checks_the_fee_before_each_transaction_it_builds(monkeypatch, tmp_path):
    """The image is registered under two version keys, a transaction each:
    for each the block is read for the check and again as the transaction is
    built."""
    w3 = _Web3(gwei(425))
    registry, function, waits = _addimage_registration(monkeypatch, tmp_path, POLYGON, w3)
    assert registry.register_securelock_image("certificate") is True
    assert len(function.built) == 2
    assert w3.eth.calls == 2 * ["get_block", "get_transaction_count", "get_block",
                                "sign_transaction", "send_raw_transaction", "wait_for_transaction_receipt"]
    assert waits == [5, 5]


def test_the_addimage_registration_of_a_network_of_legacy_transactions_checks_no_fee(monkeypatch, tmp_path):
    w3 = _Web3()
    registry, function, waits = _addimage_registration(monkeypatch, tmp_path, BLOXBERG, w3)
    assert registry.register_securelock_image("certificate") is True
    assert len(function.built) == 2
    assert w3.eth.calls == 2 * ["get_transaction_count", "sign_transaction", "send_raw_transaction",
                                "wait_for_transaction_receipt"]
    assert waits == [5, 5]


def _ecld_publish(monkeypatch, tmp_path, capsys, network, w3):
    """ecld-publish's main (commands/publish.py) on `network` with KEY in
    ECLD_PRIVATE_KEY, from the key to the publish script. The image registry
    is _image_registry's over `w3`; what follows the gas balance step is
    replaced by recorders: the ownership check, which passes, and the publish
    script (pynithy/publish.py main, which posts the session, pins the image
    and sends the registration), which ends the run with exit code 0. Every
    request of `requests` is recorded. Returns the exit code, the output, the
    recorded steps and the recorded requests."""
    registry = _image_registry(monkeypatch, tmp_path, network, w3)
    publish = importlib.import_module("ethernity_cloud_sdk_py.commands.publish")
    script = importlib.import_module("ethernity_cloud_sdk_py.commands.pynithy.publish")
    monkeypatch.setattr(publish, "config", _config(tmp_path, network))
    monkeypatch.setattr(publish, "image_registry", registry)
    reached, sent = [], []
    monkeypatch.setattr(registry, "check_image_permissions", lambda: reached.append("ownership check") or True)

    def publish_script(private_key):
        reached.append("publish script")
        raise SystemExit(0)

    monkeypatch.setattr(script, "main", publish_script)
    monkeypatch.setattr(requests.Session, "request",
                        lambda self, method, url, **kwargs: sent.append((method, url)))
    monkeypatch.setenv("ECLD_PRIVATE_KEY", KEY)
    monkeypatch.setenv("ECLD_NON_INTERACTIVE", "1")
    monkeypatch.delenv("ECLD_KEY_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as exit_:
        publish.main()
    return exit_.value.code, capsys.readouterr().out, reached, sent


def test_the_publish_stops_at_the_gas_balance_step_above_the_ceiling(monkeypatch, tmp_path, capsys):
    """With the refusal printed and exit code 1: no ownership check and no
    publish script, so no session posted and nothing pinned; no request made;
    the chain read for the balance and the block only, nothing signed or sent."""
    w3 = _Web3(gwei(426))
    code, out, reached, sent = _ecld_publish(monkeypatch, tmp_path, capsys, POLYGON, w3)
    assert code == 1
    assert f"{Spinner.CHECK}Checking gas balance\n" in out
    assert f"{Spinner.FAIL}{STEP}\nException: {REFUSAL_AT_500}\n" in out
    assert reached == [] and sent == []
    assert w3.eth.calls == ["get_balance", "get_block"]


@pytest.mark.parametrize("network", [member for member in BlockchainNetworks if member.is_eip1559],
                         ids=lambda member: member.name)
def test_the_publish_checks_the_fee_on_every_eip1559_network(monkeypatch, tmp_path, capsys, network):
    """A base fee equal to the network's default ceiling, which the default
    refuses, stops the publish at the step."""
    w3 = _Web3(gwei(network.max_fee_per_gas))
    code, out, reached, sent = _ecld_publish(monkeypatch, tmp_path, capsys, network, w3)
    assert code == 1
    assert (f"{Spinner.FAIL}{STEP}\nException: {network.display_name}: "
            f"a base fee of {network.max_fee_per_gas} gwei puts the fee per gas at ") in out
    assert reached == [] and sent == []
    assert w3.eth.calls == ["get_balance", "get_block"]


def test_the_publish_goes_on_at_a_base_fee_the_ceiling_admits(monkeypatch, tmp_path, capsys):
    w3 = _Web3(gwei(425))
    code, out, reached, sent = _ecld_publish(monkeypatch, tmp_path, capsys, POLYGON, w3)
    assert code == 0
    assert f"{Spinner.CHECK}{STEP}\n" in out
    assert reached == ["ownership check", "publish script"] and sent == []
    assert w3.eth.calls == ["get_balance", "get_block"]


def test_the_publish_goes_on_under_the_ceiling_the_variable_sets(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(VARIABLE, "600")
    w3 = _Web3(gwei(426))
    code, out, reached, _ = _ecld_publish(monkeypatch, tmp_path, capsys, POLYGON, w3)
    assert code == 0
    assert f"{Spinner.CHECK}{STEP}\n" in out
    assert reached == ["ownership check", "publish script"]


def test_the_publish_stops_at_the_gas_balance_step_on_a_variable_it_cannot_read(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(VARIABLE, "500 gwei")
    w3 = _Web3(gwei(100))
    code, out, reached, sent = _ecld_publish(monkeypatch, tmp_path, capsys, POLYGON, w3)
    assert code == 1
    assert f"{Spinner.FAIL}{STEP}\nException: {variable_refusal('500 gwei')}\n" in out
    assert reached == [] and sent == []
    assert w3.eth.calls == ["get_balance"]


@pytest.mark.parametrize("value", [pytest.param(None, id="unset"), "1", "abc"])
def test_the_publish_of_a_network_of_legacy_transactions_checks_no_fee(monkeypatch, tmp_path, capsys, value):
    if value is not None:
        monkeypatch.setenv(VARIABLE, value)
    w3 = _Web3()
    code, out, reached, _ = _ecld_publish(monkeypatch, tmp_path, capsys, BLOXBERG, w3)
    assert code == 0
    assert STEP not in out
    assert reached == ["ownership check", "publish script"]
    assert w3.eth.calls == ["get_balance"]
