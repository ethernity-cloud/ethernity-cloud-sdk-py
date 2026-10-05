"""The -unsafe variants of the bloxberg testnet and LitVM: what the SDK
derives from the network member, checked without a chain."""
import pytest

from ethernity_cloud_sdk_py.commands import run
from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks

V2_IMAGE_REGISTRY = "0xDf8cBCb1B57Fa34e7eA6b0f6B104B1aC8EF1dc53"


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


def test_only_the_unsafe_members_are_unsafe():
    for member in BlockchainNetworks:
        assert member.is_unsafe == member.name.endswith("_UNSAFE"), member.name
    assert {m.name for m in BlockchainNetworks if m.is_unsafe} == {
        "BLOXBERG_TESTNET_UNSAFE", "LITVM_LITEFORGE_UNSAFE"}


def test_the_unsafe_litvm_shares_the_litvm_chain_and_contracts():
    safe = BlockchainNetworks.LITVM_LITEFORGE
    unsafe = BlockchainNetworks.LITVM_LITEFORGE_UNSAFE
    for field in ("network", "network_type", "protocol_contract_address",
                  "image_registry_contract_address", "rpc_url", "chain_id"):
        assert getattr(unsafe, field) == getattr(safe, field), field
    assert (BlockchainNetworks.get_esr_contract_address("LITVM_LITEFORGE_UNSAFE")
            == BlockchainNetworks.get_esr_contract_address("LITVM_LITEFORGE"))
    assert unsafe.template_image["Pynithy"].trusted_zone_image == "ecld-pynithy-litvm-testnet-unsafe"


def test_litvm_is_cas_attested_by_its_own_set_and_its_unsafe_variant_is_not():
    safe = BlockchainNetworks.LITVM_LITEFORGE
    unsafe = BlockchainNetworks.LITVM_LITEFORGE_UNSAFE
    assert safe.cas_provisioned
    assert (BlockchainNetworks.get_validator_registry_address("LITVM_LITEFORGE")
            == "0xbE3759f327e8643fC5A1717fb2072A0dAcBAD29E")
    assert (BlockchainNetworks.get_session_registry_address("LITVM_LITEFORGE")
            == "0x8ad24b3F406A41a0F8D3440021792EB203957F43")
    assert not unsafe.cas_provisioned
    assert BlockchainNetworks.get_session_registry_address("LITVM_LITEFORGE_UNSAFE") == ""
    assert BlockchainNetworks.get_validator_registry_address("LITVM_LITEFORGE_UNSAFE") == ""


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


def test_run_resolves_each_network_to_the_runners():
    assert run._resolve_network("BLOXBERG_TESTNET_UNSAFE", None) == ("BLOXBERG", "TESTNET_UNSAFE")
    assert run._resolve_network("BLOXBERG_TESTNET", None) == ("BLOXBERG", "TESTNET")
    assert run._resolve_network("POLYGON_AMOY", None) == ("POLYGON", "AMOY")
    assert run._resolve_network("ETHEREUM_SEPOLIA", None) == ("ETHEREUM", "SEPOLIA")
    assert run._resolve_network("LITVM_LITEFORGE", None) == ("LITVM", "LITEFORGE")
    assert run._resolve_network("LITVM_LITEFORGE_UNSAFE", None) == ("LITVM", "LITEFORGE_UNSAFE")


def test_every_network_is_one_the_runner_knows():
    from ethernity_cloud_runner_py.enums import ECNetwork
    for member in BlockchainNetworks:
        name, kind = member.runner_network
        config = getattr(getattr(ECNetwork, name), kind, None)
        assert config is not None, f"{member.name} -> {name} {kind}"
        assert config.CHAIN_ID == member.chain_id, member.name
        assert config.PROTOCOL_ADDRESS.lower() == member.protocol_contract_address.lower(), member.name
        assert config.TRUSTEDZONE_IMAGE.endswith("-unsafe") == member.is_unsafe, member.name


def test_the_unsafe_flag_selects_the_networks_unsafe_variant():
    member = run._network_member("BLOXBERG_TESTNET", None, unsafe=True)
    assert member is BlockchainNetworks.BLOXBERG_TESTNET_UNSAFE
    assert run._network_member("BLOXBERG_TESTNET_UNSAFE", None, unsafe=True) is member
    assert (run._network_member("LITVM_LITEFORGE", None, unsafe=True)
            is BlockchainNetworks.LITVM_LITEFORGE_UNSAFE)
    with pytest.raises(ValueError):
        run._network_member("POLYGON_AMOY", None, unsafe=True)


def test_the_unsafe_run_takes_the_unsafe_trustedzone():
    member = BlockchainNetworks.BLOXBERG_TESTNET_UNSAFE
    assert run._trustedzone_for(member, None) == "etny-pynithy-testnet-unsafe"
    assert run._trustedzone_for(None, None) is None
