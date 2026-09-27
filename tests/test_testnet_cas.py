"""The CAS-attested testnet: what ecld-publish computes before it touches the
chain, checked without a chain."""
import hashlib

from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks
from ethernity_cloud_sdk_py.commands.pynithy import cas_resolver, session_registry

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
    net = BlockchainNetworks.BLOXBERG_TESTNET
    assert net.network_type == "testnet_cas"
    assert net.cas_provisioned
    assert BlockchainNetworks.get_session_registry_address("BLOXBERG_TESTNET").startswith("0x")
    assert BlockchainNetworks.get_validator_registry_address("BLOXBERG_TESTNET").startswith("0x")
    assert not BlockchainNetworks.POLYGON_MAINNET.cas_provisioned or \
        BlockchainNetworks.POLYGON_MAINNET.network_type == "mainnet"


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
