"""The testnet PoX 1.0 networks, BLOXBERG_TESTNET_POX10 (bloxberg_testnet_pox10)
and POLYGON_AMOY_POX10 (polygon_amoy_pox10): copies of the bloxberg mainnet's
and of Polygon mainnet's PoX 1.0 contracts on the bloxberg chain and on
Polygon Amoy. The SDK builds and publishes for each as for the mainnet it
copies: the securelock is signed --production and provisioned by Scontain's
CAS, with no SessionRegistry and no ValidatorRegistry, and the image registry
is a first-generation one. Their session tag and their trustedzone tag are the
network's word. No chain, no docker and no network: the chain, the image
registry and the runner are stand-ins, and ecld-init runs in a process of its
own (init.py reads .config.json from the process's CWD)."""
import json
import os
import subprocess
import sys

import pytest
import yaml

from ethernity_cloud_sdk_py.commands import run
from ethernity_cloud_sdk_py.commands.config import Config
from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks
from ethernity_cloud_sdk_py.commands.pynithy import build
from tests.test_fee_ceiling import GAS_LIMIT, _Web3, _addimage_registration, _image_registry, gwei
from tests.test_publish_build_gate import HOST, _publish_module
from tests.test_testnet_cas import _directive, _render_session

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BLOXBERG = BlockchainNetworks.BLOXBERG_TESTNET_POX10
AMOY = BlockchainNetworks.POLYGON_AMOY_POX10
POX10 = pytest.mark.parametrize("network", [BLOXBERG, AMOY], ids=lambda member: member.name)

BLOXBERG_TESTNET_ESR = "0x0Ea1728EAE108FD3B9340ae91451348E2Cc6b4E4"
KEY = "0x" + "44" * 32
API = "http://127.0.0.1:5001/api/v0"
# The fields of a member that are its chain's: the RPC, the chain id and the fees.
CHAIN_FIELDS = ("rpc_url", "chain_id", "is_eip1559", "gas_price", "max_fee_per_gas", "max_priority_fee_per_gas")
# The session a network's image registry names for its trustedzone, as the
# trustedzone pipelines name it.
TRUSTEDZONE_SESSIONS = {
    "BLOXBERG_TESTNET_POX10": "etny-pynithy-testnet-pox10-trustedzone-v3-0.0.0",
    "POLYGON_AMOY_POX10": "ecld-pynithy-amoy-pox10-trustedzone-v3-0.0.0",
}
SECURELOCK_SESSIONS = {
    "BLOXBERG_TESTNET_POX10": "demo_SECURELOCK_V3_bloxberg_testnet_pox10_3",
    "POLYGON_AMOY_POX10": "demo_SECURELOCK_V3_polygon_amoy_pox10_3",
}


@pytest.fixture(autouse=True)
def _network_default_ceiling(monkeypatch):
    monkeypatch.delenv("ECLD_MAX_FEE_PER_GAS", raising=False)


# ---- the members ----

def test_the_bloxberg_copy_names_its_contracts_on_chain_8995():
    assert BLOXBERG.display_name == "Bloxberg Testnet PoX 1.0"
    assert BLOXBERG.chain_id == 8995
    assert BLOXBERG.rpc_url == "https://bloxberg.ethernity.cloud"
    assert BLOXBERG.protocol_contract_address == "0x69A7bEB5Da2d3dA711d52E491D71e51296b08745"
    assert BLOXBERG.image_registry_contract_address == "0x7dBC1b591cc3b7136D863bc7430C6Db50E929665"
    assert BLOXBERG.template_image["Pynithy"].trusted_zone_image == "etny-pynithy-testnet-pox10"
    assert BlockchainNetworks.get_esr_contract_address("BLOXBERG_TESTNET_POX10") == BLOXBERG_TESTNET_ESR


def test_the_amoy_copy_names_its_contracts_on_chain_80002():
    assert AMOY.display_name == "Polygon Amoy PoX 1.0"
    assert AMOY.chain_id == 80002
    assert AMOY.rpc_url == "https://polygon-amoy-bor-rpc.publicnode.com"
    assert AMOY.protocol_contract_address == "0x9Aad82557c84c11C4031382d8Bf2cacF8f256a02"
    assert AMOY.image_registry_contract_address == "0xa2EE2a9118d0011AF05a6196c862D37d8c29F8eC"
    assert AMOY.template_image["Pynithy"].trusted_zone_image == "ecld-pynithy-amoy-pox10"
    assert BlockchainNetworks.get_esr_contract_address("POLYGON_AMOY_POX10") == ""


@pytest.mark.parametrize("copy, mainnet", [
    (BLOXBERG, BlockchainNetworks.BLOXBERG_MAINNET), (AMOY, BlockchainNetworks.POLYGON_MAINNET)],
    ids=["bloxberg", "amoy"])
def test_a_copy_is_provisioned_as_its_mainnet_is(copy, mainnet):
    """By Scontain's CAS: the type "mainnet", which is baked into the
    securelock, and neither registry of an ethernity-cas validator set."""
    assert copy.network == mainnet.network
    assert copy.network_type == mainnet.network_type == "mainnet"
    assert copy.cas_provisioned and mainnet.cas_provisioned
    assert not copy.is_unsafe and copy.cas_variant is None
    assert BlockchainNetworks.get_session_registry_address(copy.name) == ""
    assert BlockchainNetworks.get_validator_registry_address(copy.name) == ""
    assert copy.securelock_name("demo") == "demo"


def test_the_bloxberg_copy_has_the_bloxberg_fees_and_the_testnets_esr():
    mainnet, testnet = BlockchainNetworks.BLOXBERG_MAINNET, BlockchainNetworks.BLOXBERG_TESTNET
    for field in CHAIN_FIELDS[1:]:
        assert getattr(BLOXBERG, field) == getattr(mainnet, field), field
    assert BLOXBERG.rpc_url == testnet.rpc_url
    assert (BlockchainNetworks.get_esr_contract_address("BLOXBERG_TESTNET_POX10")
            == BlockchainNetworks.get_esr_contract_address("BLOXBERG_TESTNET"))


def test_the_amoy_copy_has_the_amoy_chain_and_fees_and_no_esr_as_polygon_mainnet():
    for field in CHAIN_FIELDS:
        assert getattr(AMOY, field) == getattr(BlockchainNetworks.POLYGON_AMOY, field), field
    assert (BlockchainNetworks.get_esr_contract_address("POLYGON_AMOY_POX10")
            == BlockchainNetworks.get_esr_contract_address("POLYGON_MAINNET") == "")


def test_no_other_member_names_a_contract_or_a_trustedzone_of_the_copies():
    for member in BlockchainNetworks:
        for copy in (BLOXBERG, AMOY):
            if member is copy:
                continue
            assert member.protocol_contract_address != copy.protocol_contract_address, member.name
            assert member.image_registry_contract_address != copy.image_registry_contract_address, member.name
            assert (member.template_image["Pynithy"].trusted_zone_image
                    != copy.template_image["Pynithy"].trusted_zone_image), member.name
            assert member.display_name != copy.display_name, member.name


# ---- the tags ----

def test_the_trustedzone_tag_is_the_networks_word():
    """The SDK registry tag the trustedzone pipelines push:
    <trustedzone image>/trustedzone:<word>."""
    assert BLOXBERG.trustedzone_tag == "bloxberg_testnet_pox10"
    assert AMOY.trustedzone_tag == "polygon_amoy_pox10"


def test_the_session_tag_is_the_networks_word_and_no_mainnets():
    assert BLOXBERG.session_tag == "bloxberg_testnet_pox10"
    assert AMOY.session_tag == "polygon_amoy_pox10"
    assert BlockchainNetworks.BLOXBERG_MAINNET.session_tag == "mainnet"
    assert BlockchainNetworks.POLYGON_MAINNET.session_tag == "mainnet"


@pytest.mark.parametrize("network, session", [
    ("BLOXBERG_TESTNET_POX10", "demo_app_SECURELOCK_V3_bloxberg_testnet_pox10_3"),
    ("POLYGON_AMOY_POX10", "demo_app_SECURELOCK_V3_polygon_amoy_pox10_3"),
    ("BLOXBERG_MAINNET", "demo_app_SECURELOCK_V3_mainnet_3"),
    ("POLYGON_MAINNET", "demo_app_SECURELOCK_V3_mainnet_3"),
    ("BLOXBERG_TESTNET", "demo_app_SECURELOCK_V3_testnet_3"),
])
def test_the_build_names_the_securelock_session_with_the_tag(monkeypatch, tmp_path, network, session):
    """build.update_dockerfile, from the directory holding securelock/: the
    project name, the session tag and the version, with '-' and '/' as '_'."""
    cfg = Config(tmp_path / ".config.json")
    for key, value in {"PROJECT_NAME": "demo-app", "BLOCKCHAIN_NETWORK": network, "VERSION": 3,
                       "DOCKER_REPO_URL": "registry.example/apps", "BASE_IMAGE_TAG": "python"}.items():
        cfg.write(key, value)
    monkeypatch.setattr(build, "config", cfg)
    (tmp_path / "securelock").mkdir()
    (tmp_path / "securelock" / "Dockerfile.base.tpl").write_text(
        "FROM __DOCKER_REPO_URL__:__BASE_IMAGE_TAG__\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert build.update_dockerfile() is True
    assert cfg.read("SECURELOCK_SESSION") == session
    assert (tmp_path / "securelock" / "Dockerfile.base").read_text(encoding="utf-8") == (
        "FROM registry.example/apps:python\n")


# ---- the session and the compose of a publish ----

@POX10
def test_the_session_lists_its_advisories_as_a_mainnets_does(tmp_path, network):
    out = _render_session(tmp_path, network.name)
    assert _directive(out, "ignore_advisories") == 'ignore_advisories: ["INTEL-SA-00615"]'
    assert "debug-mode" not in _directive(out, "tolerate")
    assert "__IGNORE_ADVISORIES__" not in out


class _TrustedzoneRegistry:
    """An image registry that records one trustedzone image under the
    network's trustedzone name, with `session` as its CAS session."""

    def __init__(self, session):
        self.session = session
        self.asked = []

    def get_trusted_zone_hash(self, trusted_zone_image, version):
        self.asked.append((trusted_zone_image, version))
        return "QmTrustedzone"

    def get_trustezone_image_session(self, ipfs_hash):
        self.asked.append(ipfs_hash)
        return self.session


@POX10
def test_both_composes_name_scontains_cas_and_the_two_sessions(monkeypatch, tmp_path, network):
    trustedzone = network.template_image["Pynithy"].trusted_zone_image
    publish = _publish_module(monkeypatch, tmp_path, BLOCKCHAIN_NETWORK=network.name,
                              SECURELOCK_SESSION=SECURELOCK_SESSIONS[network.name],
                              TRUSTED_ZONE_IMAGE=trustedzone)
    registry = _TrustedzoneRegistry(TRUSTEDZONE_SESSIONS[network.name])
    monkeypatch.setattr(publish, "image_registry", registry)
    dest = tmp_path / "build" / "securelock" / "run"
    assert publish.update_docker_compose_files(dest, HOST) is True
    assert registry.asked == [(trustedzone, "v3"), "QmTrustedzone"]
    for name in ("docker-compose.yml", "docker-compose-final.yml"):
        services = yaml.safe_load((dest / name).read_text(encoding="utf-8"))["services"]
        assert list(services) == ["las", "etny-securelock", "etny-trustedzone"], name
        securelock = services["etny-securelock"]["environment"]
        zone = services["etny-trustedzone"]["environment"]
        for environment in (securelock, zone):
            assert "SCONE_CAS_ADDR=scone-cas.cf" in environment, name
            assert "SCONE_LAS_ADDR=las" in environment, name
        assert f"SCONE_CONFIG_ID={SECURELOCK_SESSIONS[network.name]}/application" in securelock, name
        assert f"SCONE_CONFIG_ID={TRUSTEDZONE_SESSIONS[network.name]}/application" in zone, name


@POX10
def test_a_publish_without_a_recorded_trustedzone_session_renders_no_compose(monkeypatch, tmp_path, capsys, network):
    publish = _publish_module(monkeypatch, tmp_path, BLOCKCHAIN_NETWORK=network.name,
                              TRUSTED_ZONE_IMAGE=network.template_image["Pynithy"].trusted_zone_image)
    monkeypatch.setattr(publish, "image_registry", _TrustedzoneRegistry(""))
    assert publish.update_docker_compose_files(tmp_path / "build" / "securelock" / "run", HOST) is False
    assert "the image registry records no trustedzone session for" in capsys.readouterr().out


# ---- the image registry's transactions ----

def test_the_bloxberg_copys_registration_is_sent_at_the_bloxberg_gas_price(monkeypatch, tmp_path):
    w3 = _Web3()
    registry, function, _ = _addimage_registration(monkeypatch, tmp_path, BLOXBERG, w3)
    assert registry.image_registry_address == "0x7dBC1b591cc3b7136D863bc7430C6Db50E929665"
    assert registry.register_securelock_image("certificate") is True
    assert function.built == 2 * [{"nonce": 7, "chainId": 8995, "from": registry.acct.address,
                                   "gasPrice": 2_000_000, "gas": 9_000_000}]
    assert "get_block" not in w3.eth.calls


def test_the_amoy_copys_transactions_are_type_2_within_amoys_ceiling(monkeypatch, tmp_path):
    """The ceiling of 500 gwei: a base fee of 425 gwei gives a fee of 499.5
    gwei, which is sent; one of 426 gwei gives 500.6 gwei, which is refused."""
    registry = _image_registry(monkeypatch, tmp_path, AMOY, _Web3(gwei(425)))
    assert registry.image_registry_address == "0xa2EE2a9118d0011AF05a6196c862D37d8c29F8eC"
    assert registry._transaction_options(GAS_LIMIT) == {
        "nonce": 7, "chainId": 80002, "from": registry.acct.address,
        "type": 2, "maxFeePerGas": 499_500_000_000, "maxPriorityFeePerGas": gwei(32)}
    refused = _image_registry(monkeypatch, tmp_path, AMOY, _Web3(gwei(426)))
    with pytest.raises(Exception) as refusal:
        refused._transaction_options(GAS_LIMIT)
    assert str(refusal.value) == (
        "Polygon Amoy PoX 1.0: a base fee of 426 gwei puts the fee per gas at 500.6 gwei, above the network's "
        "default ceiling of 500 gwei (ECLD_MAX_FEE_PER_GAS sets another, in whole gwei)")


# ---- ecld-build's ESR gate ----

def _esr_gate(monkeypatch, tmp_path, network):
    cfg = Config(tmp_path / ".config.json")
    cfg.write("BLOCKCHAIN_NETWORK", network)
    cfg.write_esr({"enabled": True, "contract_address": "", "wallet_address": ""})
    monkeypatch.setattr(build, "config", cfg)
    return cfg


def test_an_esr_build_for_the_bloxberg_copy_takes_the_bloxberg_testnets_registry(monkeypatch, tmp_path):
    cfg = _esr_gate(monkeypatch, tmp_path, "BLOXBERG_TESTNET_POX10")
    assert build.validate_esr_config()["contract_address"] == BLOXBERG_TESTNET_ESR
    assert cfg.read_esr()["contract_address"] == BLOXBERG_TESTNET_ESR


def test_an_esr_build_for_the_amoy_copy_is_refused(monkeypatch, tmp_path, capsys):
    _esr_gate(monkeypatch, tmp_path, "POLYGON_AMOY_POX10")
    with pytest.raises(SystemExit) as exit_:
        build.validate_esr_config()
    assert exit_.value.code == 1
    assert "ERROR: ESR is enabled but no ESR registry is deployed on POLYGON_AMOY_POX10." in capsys.readouterr().out


# ---- ecld-init ----

def _ecld_init(tmp_path, network, **more):
    """ecld-init driven through ECLD_* variables with `network` as
    ECLD_BLOCKCHAIN_NETWORK; returns its output and the .config.json it wrote."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("ECLD_")}
    env.update(PYTHONPATH=REPO_ROOT, PYTHONIOENCODING="utf-8",
               ECLD_NON_INTERACTIVE="1", ECLD_PROJECT_NAME="demo",
               ECLD_BLOCKCHAIN_NETWORK=network, ECLD_DAPP_TYPE="Pynithy",
               ECLD_APP_TEMPLATE="no", **more)
    done = subprocess.run(
        [sys.executable, "-c", "from ethernity_cloud_sdk_py.cli import main_init; main_init()"],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", check=True)
    return done.stdout.replace("\r\n", "\n"), json.loads((tmp_path / ".config.json").read_text())


@pytest.mark.parametrize("given, name, display", [
    ("BLOXBERG_TESTNET_POX10", "BLOXBERG_TESTNET_POX10", "Bloxberg Testnet PoX 1.0"),
    ("bloxberg_testnet_pox10", "BLOXBERG_TESTNET_POX10", "Bloxberg Testnet PoX 1.0"),
    ("Bloxberg Testnet PoX 1.0", "BLOXBERG_TESTNET_POX10", "Bloxberg Testnet PoX 1.0"),
    ("POLYGON_AMOY_POX10", "POLYGON_AMOY_POX10", "Polygon Amoy PoX 1.0"),
    ("Polygon Amoy PoX 1.0", "POLYGON_AMOY_POX10", "Polygon Amoy PoX 1.0"),
])
def test_ecld_init_takes_a_copy_by_its_member_name_its_word_or_its_display_name(tmp_path, given, name, display):
    out, written = _ecld_init(tmp_path, given)
    assert written["BLOCKCHAIN_NETWORK"] == name
    assert f"Checking if the project named demo is available on the {display} network" in out
    assert "WITHOUT a CAS" not in out


def test_ecld_init_with_esr_on_the_bloxberg_copy_stores_the_bloxberg_testnets_registry(tmp_path):
    out, written = _ecld_init(tmp_path, "BLOXBERG_TESTNET_POX10", ECLD_ESR_ENABLE="1")
    assert written["ESR"]["enabled"] is True
    assert written["ESR"]["contract_address"] == BLOXBERG_TESTNET_ESR
    assert f"ESR registry contract address: {BLOXBERG_TESTNET_ESR} (shipped with the SDK)" in out


# ---- ecld-run ----

class _Runner:
    """Records the network it is built for and its storage call, and answers
    run() with a SUCCESS result."""
    built = []
    calls = []
    order_id = 7
    do_request_id = 9

    def __init__(self, network_name, network_type):
        _Runner.built.append((network_name, network_type))

    def set_log_level(self, level):
        pass

    def set_private_key(self, key):
        pass

    def set_public_intake(self):
        _Runner.calls.append(("intake",))

    def set_storage_ipfs(self, url, token=""):
        _Runner.calls.append(("ipfs", url, token))

    def connect(self):
        pass

    def run(self, *args, **kwargs):
        _Runner.calls.append(("run", args[1], kwargs["trustedzone_enclave"]))

    def is_running(self):
        return False

    def get_state(self):
        return {"status": "SUCCESS", "progress": "done"}

    def get_result(self):
        return {"value": "ok", "result_task_code": "SUCCESS", "result_task_code_int": 0}


@pytest.fixture
def ecld_run(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECLD_PRIVATE_KEY", KEY)
    monkeypatch.delenv("ECLD_IPFS_TOKEN", raising=False)
    monkeypatch.setattr("ethernity_cloud_runner_py.runner.EthernityCloudRunner", _Runner)
    _Runner.built, _Runner.calls = [], []
    return lambda *argv: run.main(["--securelock", "demo", *argv, "hello()"])


@pytest.mark.parametrize("network, runner_network, trustedzone", [
    ("BLOXBERG_TESTNET_POX10", ("BLOXBERG", "TESTNET_POX10"), "etny-pynithy-testnet-pox10"),
    ("POLYGON_AMOY_POX10", ("POLYGON", "AMOY_POX10"), "ecld-pynithy-amoy-pox10"),
])
def test_ecld_run_runs_a_copy_on_the_runners_network_and_the_copys_trustedzone(ecld_run, network, runner_network,
                                                                              trustedzone):
    assert run._resolve_network(network, None) == runner_network
    assert run._trustedzone_for(BlockchainNetworks[network], None) == trustedzone
    assert ecld_run("--network", network, "--ipfs", "http://127.0.0.1:5001", "--ipfs-token", "writer-token") == 0
    assert _Runner.built == [runner_network]
    assert _Runner.calls == [("ipfs", API, "writer-token"), ("run", "demo", trustedzone)]


@pytest.mark.parametrize("network, key", [
    ("BLOXBERG_TESTNET_POX10", "bloxberg_testnet_pox10"), ("POLYGON_AMOY_POX10", "polygon_amoy_pox10")])
def test_ecld_run_on_the_payload_intake_is_a_usage_error_naming_ipfs(ecld_run, capsys, network, key):
    with pytest.raises(SystemExit) as raised:
        ecld_run("--network", network)
    assert raised.value.code == 2
    error = capsys.readouterr().err
    assert f"not {key}: pass --ipfs <Kubo RPC API URL>" in error
    assert _Runner.built == [] and _Runner.calls == []


@POX10
def test_a_copy_has_no_unsafe_variant(ecld_run, capsys, network):
    with pytest.raises(ValueError, match=f"{network.name} has no -unsafe variant"):
        run._network_member(network.name, None, unsafe=True)
    with pytest.raises(SystemExit) as raised:
        ecld_run("--network", network.name, "--unsafe", "--ipfs", "http://127.0.0.1:5001")
    assert raised.value.code == 2
    assert f"{network.name} has no -unsafe variant" in capsys.readouterr().err
    assert _Runner.built == []
