"""ecld-init: the notice printed after an -unsafe network is chosen. ecld-init
is driven as unattended_acceptance.sh drives it, through ECLD_* variables, in a
process of its own (init.py reads .config.json from the process's CWD); no
chain is reached."""
import json
import os
import subprocess
import sys

import pytest

from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ecld_init(tmp_path, network):
    env = {name: value for name, value in os.environ.items() if not name.startswith("ECLD_")}
    env.update(PYTHONPATH=REPO_ROOT, PYTHONIOENCODING="utf-8",
               ECLD_NON_INTERACTIVE="1", ECLD_PROJECT_NAME="demo",
               ECLD_BLOCKCHAIN_NETWORK=network, ECLD_DAPP_TYPE="Pynithy",
               ECLD_APP_TEMPLATE="no")
    run = subprocess.run(
        [sys.executable, "-c", "from ethernity_cloud_sdk_py.cli import main_init; main_init()"],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", check=True)
    written = json.loads((tmp_path / ".config.json").read_text())
    assert written["BLOCKCHAIN_NETWORK"] == network
    return run.stdout.replace("\r\n", "\n")


def _notice(unsafe, sibling):
    return (
        f"{unsafe} runs your dApp WITHOUT a CAS, on the chain and contracts of {sibling},\n"
        "for SGX platforms the CAS cannot attest (EPID-only, SGX1). Its enclaves are debug-signed and\n"
        "sign their own certificates from their measurement: a result proves which image ran, not that\n"
        f"an enclave ran it. The securelock is registered as <project>-unsafe; choose {sibling} for\n"
        "a CAS-attested run.\n"
    )


@pytest.mark.parametrize("network", ["BLOXBERG_TESTNET_UNSAFE", "LITVM_LITEFORGE_UNSAFE", "ETHEREUM_SEPOLIA_UNSAFE"])
def test_an_unsafe_choice_prints_the_notice_naming_the_cas_attested_network(tmp_path, network):
    member = BlockchainNetworks[network]
    out = _ecld_init(tmp_path, network)
    assert _notice(member.display_name, member.cas_variant.display_name) in out


def test_a_cas_attested_choice_prints_no_notice(tmp_path):
    out = _ecld_init(tmp_path, "BLOXBERG_TESTNET")
    assert "WITHOUT a CAS" not in out
    assert "a CAS-attested run." not in out
    assert "Checking if the project named demo is available on the Bloxberg Testnet network" in out
