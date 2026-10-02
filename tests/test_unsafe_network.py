"""The -unsafe variant of the bloxberg testnet: what the SDK derives from the
network member, checked without a chain."""
import pytest

from ethernity_cloud_sdk_py.commands import run
from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks

V2_IMAGE_REGISTRY = "0x99A84C624C028bdf0a855A1E9E3f2fcf7275B3D8"


def test_the_unsafe_testnet_shares_the_testnet_chain_and_contracts():
    safe = BlockchainNetworks.BLOXBERG_TESTNET
    unsafe = BlockchainNetworks.BLOXBERG_TESTNET_UNSAFE
    assert unsafe is not safe, "a member with the same values would be an alias"
    for field in ("network", "network_type", "protocol_contract_address",
                  "image_registry_contract_address", "rpc_url", "chain_id"):
        assert getattr(unsafe, field) == getattr(safe, field), field
    assert safe.image_registry_contract_address == V2_IMAGE_REGISTRY
    assert (BlockchainNetworks.get_esr_contract_address("BLOXBERG_TESTNET_UNSAFE")
            == BlockchainNetworks.get_esr_contract_address("BLOXBERG_TESTNET"))


def test_the_unsafe_testnet_has_no_cas():
    unsafe = BlockchainNetworks.BLOXBERG_TESTNET_UNSAFE
    assert unsafe.is_unsafe
    assert not unsafe.cas_provisioned
    assert BlockchainNetworks.get_session_registry_address("BLOXBERG_TESTNET_UNSAFE") == ""
    assert BlockchainNetworks.get_validator_registry_address("BLOXBERG_TESTNET_UNSAFE") == ""
    assert unsafe.template_image["Pynithy"].trusted_zone_image == "etny-pynithy-testnet-unsafe"


def test_only_the_unsafe_member_is_unsafe():
    for member in BlockchainNetworks:
        assert member.is_unsafe == (member.name == "BLOXBERG_TESTNET_UNSAFE"), member.name


def test_the_securelock_is_registered_as_project_unsafe():
    unsafe = BlockchainNetworks.BLOXBERG_TESTNET_UNSAFE
    safe = BlockchainNetworks.BLOXBERG_TESTNET
    assert unsafe.securelock_name("demo") == "demo-unsafe"
    assert unsafe.securelock_name("demo-unsafe") == "demo-unsafe", "the runtime .env holds the registered name"
    assert safe.securelock_name("demo") == "demo"


def test_the_two_variants_never_share_a_session_name():
    assert BlockchainNetworks.BLOXBERG_TESTNET.session_tag == "testnet"
    assert BlockchainNetworks.BLOXBERG_TESTNET_UNSAFE.session_tag == "testnet_unsafe"
    assert BlockchainNetworks.BLOXBERG_MAINNET.session_tag == "mainnet"


def test_run_resolves_the_unsafe_network_to_the_runners_bloxberg_testnet():
    assert run._resolve_network("BLOXBERG_TESTNET_UNSAFE", None) == ("BLOXBERG", "TESTNET")
    assert run._resolve_network("BLOXBERG_TESTNET", None) == ("BLOXBERG", "TESTNET")
    assert run._resolve_network("POLYGON_AMOY", None) == ("POLYGON", "TESTNET")


def test_the_unsafe_flag_selects_the_networks_unsafe_variant():
    member = run._network_member("BLOXBERG_TESTNET", None, unsafe=True)
    assert member is BlockchainNetworks.BLOXBERG_TESTNET_UNSAFE
    assert run._network_member("BLOXBERG_TESTNET_UNSAFE", None, unsafe=True) is member
    with pytest.raises(ValueError):
        run._network_member("POLYGON_AMOY", None, unsafe=True)


def test_the_unsafe_run_takes_the_unsafe_trustedzone():
    member = BlockchainNetworks.BLOXBERG_TESTNET_UNSAFE
    assert run._trustedzone_for(member, None) == "etny-pynithy-testnet-unsafe"
    assert run._trustedzone_for(None, None) is None
