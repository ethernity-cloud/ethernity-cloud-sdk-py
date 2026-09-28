"""The CAS-attested testnet: what ecld-publish computes before it touches the
chain, checked without a chain."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap

from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks
from ethernity_cloud_sdk_py.commands.pynithy import cas_resolver, session_registry

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO_ROOT, "ethernity_cloud_sdk_py", "commands", "pynithy",
                        "run", "etny-securelock-test.yaml.tpl")

SESSION = """name: demo-SECURELOCK-V3-testnet-7
version: "0.3"

security:
  attestation:
    tolerate: [hyperthreading, outdated-tcb, software-hardening-needed]
    ignore_advisories: ["INTEL-SA-00615"]

services:
   - name: application
     image_name: application_image
     mrenclaves: [ "a1b2c3" ]
     command: /usr/local/bin/python /etny-securelock/securelock.py
"""


def test_bloxberg_testnet_is_cas_provisioned():
    """The plain "testnet" type; CAS provisioning follows the SessionRegistry
    being configured, not a special type name."""
    net = BlockchainNetworks.BLOXBERG_TESTNET
    assert net.network_type == "testnet"
    assert net.cas_provisioned
    assert BlockchainNetworks.get_session_registry_address("BLOXBERG_TESTNET").startswith("0x")
    assert BlockchainNetworks.get_validator_registry_address("BLOXBERG_TESTNET").startswith("0x")


def test_a_testnet_without_a_session_registry_self_signs():
    for name in ("POLYGON_AMOY", "IOTEX_TESTNET", "ETHEREUM_SEPOLIA"):
        net = BlockchainNetworks[name]
        assert net.network_type == "testnet"
        assert not net.cas_provisioned
        assert BlockchainNetworks.get_session_registry_address(name) == ""


def test_mainnets_are_cas_provisioned():
    assert BlockchainNetworks.BLOXBERG_MAINNET.cas_provisioned
    assert BlockchainNetworks.POLYGON_MAINNET.cas_provisioned


def test_session_rules_follow_the_body():
    name, rules = session_registry.parse_name_and_rules(SESSION)
    tolerate, ignore_adv, mrenclaves, min_isvsvn, debug_allowed, skip_quote = rules
    assert name == "demo-SECURELOCK-V3-testnet-7"
    assert tolerate == ["hyperthreading", "outdated-tcb", "software-hardening-needed"]
    assert ignore_adv == ["INTEL-SA-00615"]
    assert mrenclaves == ["a1b2c3"]
    assert (min_isvsvn, debug_allowed, skip_quote) == (0, False, False)


def test_debug_tolerance_is_read_from_the_body():
    _, rules = session_registry.parse_name_and_rules(
        SESSION.replace("hyperthreading,", "debug-mode, hyperthreading,"))
    assert rules[4] is True


def test_session_hash_is_sha256_of_the_exact_bytes():
    body = SESSION.encode()
    assert session_registry.session_hash(body) == hashlib.sha256(body).digest()


def test_multiaddr_parsing_and_rest_pairing():
    assert cas_resolver.parse_multiaddr("/dns4/cas.ethernity.cloud/tcp/19766") == ("cas.ethernity.cloud", 19766)
    assert cas_resolver.parse_multiaddr("/ip4/10.0.0.5/tcp/19765") == ("10.0.0.5", 19765)
    # No Tor proxy on a developer machine: onion adverts are not dialled.
    assert cas_resolver.parse_multiaddr("/onion3/abcdefghijklmnop/tcp/19765") is None
    assert cas_resolver.parse_multiaddr("/dns4/host/udp/19765") is None
    assert cas_resolver.parse_multiaddr("garbage") is None
    assert cas_resolver.rest_port_for(19765) == 9081
    assert cas_resolver.rest_port_for(19767) == 9083
    assert cas_resolver.rest_port_for(18765) == 9081


def test_cas_address_override_wins(monkeypatch):
    monkeypatch.setenv("ECLD_CAS_ADDR", "cas.local:19765")
    assert cas_resolver.cas_address_for("http://unused", "") == "cas.local:19765"


def _render_session(tmp_path, network):
    """Run publish.process_yaml_template for `network` in its own directory.

    A subprocess because publish.py builds an ImageRegistry at import time and
    reads .config.json from the process's CWD, so two networks cannot be
    rendered in one interpreter.
    """
    work = tmp_path / network
    work.mkdir()
    shutil.copy(TEMPLATE, work / "etny-securelock-test.yaml.tpl")
    (work / ".config.json").write_text(json.dumps({
        "PROJECT_NAME": "demo", "BLOCKCHAIN_NETWORK": network, "VERSION": 1,
        "MRENCLAVE_SECURELOCK": "aa", "SECURELOCK_SESSION": "demo_SECURELOCK_V3_1",
        "PREDECESSOR_HASH_SECURELOCK": "",
    }))
    script = textwrap.dedent("""
        from ethernity_cloud_sdk_py.commands.pynithy import publish
        publish.config.load()
        publish.process_yaml_template("etny-securelock-test.yaml.tpl", "out.yaml")
    """)
    env = dict(os.environ, PYTHONPATH=REPO_ROOT, PYTHONIOENCODING="utf-8")
    subprocess.run([sys.executable, "-c", script], cwd=work, env=env, check=True,
                   capture_output=True)
    return (work / "out.yaml").read_text()


def _directive(out, key):
    """The rendered `key:` line, ignoring the comments above it -- those
    mention both networks' values and would match either assertion."""
    return next(l.strip() for l in out.splitlines() if l.strip().startswith(f"{key}:"))


def test_a_testnet_session_ignores_every_advisory(tmp_path):
    """A testnet platform is whatever an operator has; refusing it for a TCB
    level teaches nothing about the dApp under test, and a developer cannot
    drive volunteer hardware to a given BIOS."""
    out = _render_session(tmp_path, "BLOXBERG_TESTNET")
    assert _directive(out, "ignore_advisories") == 'ignore_advisories: ["*"]'
    assert "__IGNORE_ADVISORIES__" not in out


def test_a_mainnet_session_lists_its_advisories(tmp_path):
    """Each entry is a decision that a KNOWN advisory does not disqualify a
    production platform; a future one must be reviewed, not inherited."""
    out = _render_session(tmp_path, "BLOXBERG_MAINNET")
    assert _directive(out, "ignore_advisories") == 'ignore_advisories: ["INTEL-SA-00615"]'
    assert "__IGNORE_ADVISORIES__" not in out


def test_no_session_tolerates_a_debug_enclave(tmp_path):
    """SGX debug mode makes enclave memory inspectable. Neither network may
    admit it -- on a testnet the enclaves are production-signed too."""
    for network in ("BLOXBERG_TESTNET", "BLOXBERG_MAINNET"):
        out = _render_session(tmp_path, network)
        tolerate = _directive(out, "tolerate")
        assert "debug-mode" not in tolerate, f"{network}: {tolerate}"
