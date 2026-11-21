"""The network table, held by value: every BlockchainNetworks member in the
order ecld-init lists them, every value of each, the side maps keyed by member
name (the ESR, ValidatorRegistry and SessionRegistry addresses, the -unsafe
variants, the trustedzone tags, the session tags, the runner networks) and
what the SDK derives from a member (its session tag, its trustedzone tag,
whether a CAS provisions its securelock, whether it is an -unsafe variant, the
runner network it maps to). An identifier or a value of a published network
that changes fails here. No chain; the runner's table is read for the entry
each member maps to."""
import pytest

from ethernity_cloud_sdk_py.commands import enums
from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks, dAppTypes

# (display name, network, network type, PoX, image registry, RPC, chain id,
# EIP-1559, gas price, max fee per gas, max priority fee per gas, Pynithy
# trustedzone) of every member, in the order of the enum, which is the order
# of ecld-init's list. The fees are in gwei.
MEMBERS = {
    "BLOXBERG_MAINNET": (
        "Bloxberg Mainnet", "bloxberg", "mainnet",
        "0x549A6E06BB2084100148D50F51CF77a3436C3Ae7", "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31",
        "https://core.bloxberg.org", 8995, False, 0.002, 0.002, 0.0001, "etny-pynithy"),
    "BLOXBERG_TESTNET": (
        "Bloxberg Testnet", "bloxberg", "testnet",
        "0x830c781F0352050343211B6BFA83600B3e77647C", "0xa372a6e1Eb7Fcf343AF6b91E809b900C55001CD1",
        "https://bloxberg.ethernity.cloud", 8995, False, 0.002, 0.002, 0.0001, "etny-pynithy-testnet"),
    "BLOXBERG_TESTNET_UNSAFE": (
        "Bloxberg Testnet unsafe (no CAS)", "bloxberg", "testnet",
        "0x830c781F0352050343211B6BFA83600B3e77647C", "0xa372a6e1Eb7Fcf343AF6b91E809b900C55001CD1",
        "https://bloxberg.ethernity.cloud", 8995, False, 0.002, 0.002, 0.0001, "etny-pynithy-testnet-unsafe"),
    "POLYGON_MAINNET": (
        "Polygon Mainnet", "polygon", "mainnet",
        "0x439945BE73fD86fcC172179021991E96Beff3Cc4", "0x689f3806874d3c8A973f419a4eB24e6fBA7E830F",
        "https://polygon-bor-rpc.publicnode.com", 137, True, 200, 500, 32, "ecld-pynithy"),
    "POLYGON_AMOY": (
        "Polygon Amoy", "polygon", "testnet",
        "0xd1182deA61CDa785C320cdBE5888EaC4178cAAF2", "0x0C0f52ebd72cd2d2cBe94F385c9e23cec528b706",
        "https://polygon-amoy-bor-rpc.publicnode.com", 80002, True, 100, 64, 32, "ecld-pynithy-amoy"),
    "POLYGON_AMOY_UNSAFE": (
        "Polygon Amoy unsafe (no CAS)", "polygon", "testnet",
        "0xd1182deA61CDa785C320cdBE5888EaC4178cAAF2", "0x0C0f52ebd72cd2d2cBe94F385c9e23cec528b706",
        "https://polygon-amoy-bor-rpc.publicnode.com", 80002, True, 100, 64, 32, "ecld-pynithy-amoy-unsafe"),
    "IOTEX_TESTNET": (
        "IoTeX Testnet", "iotex", "testnet",
        "0xD56385A97413Ed80E28B1b54A193b98F2C49c975", "0xa7467A6391816be9367a1cC52E0ef0c15FfE3cCC",
        "https://babel-api.testnet.iotex.io", 4690, True, 5, 1500, 1, "ecld-pynithy-iotex-testnet"),
    "ETHEREUM_SEPOLIA": (
        "Ethereum Sepolia", "ethereum", "testnet",
        "0x798975F9FdD90A21Cb11Ac26F1377e1542c58f9c", "0xa7E43a269E3307cf9d246b07df2B89CAA1b4C42F",
        "https://ethereum-sepolia-rpc.publicnode.com", 11155111, True, 5, 15, 1, "ecld-pynithy-ethereum-sepolia"),
    "ETHEREUM_SEPOLIA_UNSAFE": (
        "Ethereum Sepolia unsafe (no CAS)", "ethereum", "testnet",
        "0x798975F9FdD90A21Cb11Ac26F1377e1542c58f9c", "0xa7E43a269E3307cf9d246b07df2B89CAA1b4C42F",
        "https://ethereum-sepolia-rpc.publicnode.com", 11155111, True, 5, 15, 1,
        "ecld-pynithy-ethereum-sepolia-unsafe"),
    "LITVM_LITEFORGE": (
        "LitVM LiteForge", "litvm", "testnet",
        "0xE35991de1f2A435e29E70406FA2781b1AffCb168", "0xAf8Ce29103E1f59feDD49688ff070Ffc2346AF1D",
        "https://rpc.testnet.liteforge.zeeve.net/", 4441, True, 2, 2, 0, "ecld-pynithy-litvm-testnet"),
    "LITVM_LITEFORGE_UNSAFE": (
        "LitVM LiteForge unsafe (no CAS)", "litvm", "testnet",
        "0xE35991de1f2A435e29E70406FA2781b1AffCb168", "0xAf8Ce29103E1f59feDD49688ff070Ffc2346AF1D",
        "https://rpc.testnet.liteforge.zeeve.net/", 4441, True, 2, 2, 0, "ecld-pynithy-litvm-testnet-unsafe"),
    "BLOXBERG_TESTNET_POX10": (
        "Bloxberg Testnet PoX 1.0", "bloxberg", "mainnet",
        "0x69A7bEB5Da2d3dA711d52E491D71e51296b08745", "0x7dBC1b591cc3b7136D863bc7430C6Db50E929665",
        "https://bloxberg.ethernity.cloud", 8995, False, 0.002, 0.002, 0.0001, "etny-pynithy-testnet-pox10"),
    "POLYGON_AMOY_POX10": (
        "Polygon Amoy PoX 1.0", "polygon", "mainnet",
        "0x9Aad82557c84c11C4031382d8Bf2cacF8f256a02", "0xa2EE2a9118d0011AF05a6196c862D37d8c29F8eC",
        "https://polygon-amoy-bor-rpc.publicnode.com", 80002, True, 100, 64, 32, "ecld-pynithy-amoy-pox10"),
}

# The template of every member's Pynithy trustedzone, apart from its image.
DOCKER_REPO_URL = "registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/sconecuratedimages/apps"
BASE_IMAGE_TAG = "python-3.14.6-alpine3.24-scone6.0.7"

BLOXBERG_ESR = "0x4Bf5cDE3BFD73dd10B707f8B123Ba631D2EBEAD2"
BLOXBERG_TESTNET_ESR = "0x0Ea1728EAE108FD3B9340ae91451348E2Cc6b4E4"
LITVM_ESR = "0x709052Fe77Af543d3d9FE2Ac06a15c635c8D4Be5"
SEPOLIA_ESR = "0x9927809B61122B2af3f3b3A3303875e0687b8eE3"
AMOY_ESR = "0x1DC58496ed1cA66FD1E0b51fA28A9E7451970843"

ESR_CONTRACT_ADDRESSES = {
    "BLOXBERG_MAINNET": BLOXBERG_ESR,
    "BLOXBERG_TESTNET": BLOXBERG_TESTNET_ESR,
    "BLOXBERG_TESTNET_UNSAFE": BLOXBERG_TESTNET_ESR,
    "LITVM_LITEFORGE": LITVM_ESR,
    "LITVM_LITEFORGE_UNSAFE": LITVM_ESR,
    "ETHEREUM_SEPOLIA": SEPOLIA_ESR,
    "ETHEREUM_SEPOLIA_UNSAFE": SEPOLIA_ESR,
    "POLYGON_AMOY": AMOY_ESR,
    "POLYGON_AMOY_UNSAFE": AMOY_ESR,
    "POLYGON_MAINNET": "",
    "IOTEX_TESTNET": "",
    "BLOXBERG_TESTNET_POX10": BLOXBERG_TESTNET_ESR,
    "POLYGON_AMOY_POX10": "",
}
VALIDATOR_REGISTRY_ADDRESSES = {
    "BLOXBERG_TESTNET": "0xc346BfFC83f318FbbC34987cEF85716a3f7dCcB0",
    "LITVM_LITEFORGE": "0xbE3759f327e8643fC5A1717fb2072A0dAcBAD29E",
    "ETHEREUM_SEPOLIA": "0x8ad24b3F406A41a0F8D3440021792EB203957F43",
    "POLYGON_AMOY": "0x48079D66B39cD3213587d3b5300BCEc401df6676",
}
SESSION_REGISTRY_ADDRESSES = {
    "BLOXBERG_TESTNET": "0x78f0c5E92E7A9Ec2f2A36A4509ED4a75D0f9463B",
    "LITVM_LITEFORGE": "0xfA70B5Df7C773A21164cAc5413704117e4dEaB76",
    "ETHEREUM_SEPOLIA": "0xDbB249E108d19fC1691483Bf897bA8BA0dA99c06",
    "POLYGON_AMOY": "0x059662030fEeB4Ea8Fc7b05951A578d527a827e5",
}
UNSAFE_NETWORKS = {
    "BLOXBERG_TESTNET_UNSAFE": "BLOXBERG_TESTNET",
    "LITVM_LITEFORGE_UNSAFE": "LITVM_LITEFORGE",
    "ETHEREUM_SEPOLIA_UNSAFE": "ETHEREUM_SEPOLIA",
    "POLYGON_AMOY_UNSAFE": "POLYGON_AMOY",
}
TRUSTEDZONE_TAGS = {
    "BLOXBERG_MAINNET": "bloxberg",
    "POLYGON_MAINNET": "polygon",
    "POLYGON_AMOY": "amoy",
    "POLYGON_AMOY_UNSAFE": "amoy_unsafe",
}
SESSION_TAGS = {
    "BLOXBERG_TESTNET_POX10": "bloxberg_testnet_pox10",
    "POLYGON_AMOY_POX10": "polygon_amoy_pox10",
}
RUNNER_NETWORKS = {
    "BLOXBERG_MAINNET": ("BLOXBERG", "MAINNET"),
    "BLOXBERG_TESTNET": ("BLOXBERG", "TESTNET"),
    "BLOXBERG_TESTNET_UNSAFE": ("BLOXBERG", "TESTNET_UNSAFE"),
    "POLYGON_MAINNET": ("POLYGON", "MAINNET"),
    "POLYGON_AMOY": ("POLYGON", "AMOY"),
    "POLYGON_AMOY_UNSAFE": ("POLYGON", "AMOY_UNSAFE"),
    "IOTEX_TESTNET": ("IOTEX", "TESTNET"),
    "ETHEREUM_SEPOLIA": ("ETHEREUM", "SEPOLIA"),
    "ETHEREUM_SEPOLIA_UNSAFE": ("ETHEREUM", "SEPOLIA_UNSAFE"),
    "LITVM_LITEFORGE": ("LITVM", "LITEFORGE"),
    "LITVM_LITEFORGE_UNSAFE": ("LITVM", "LITEFORGE_UNSAFE"),
    "BLOXBERG_TESTNET_POX10": ("BLOXBERG", "TESTNET_POX10"),
    "POLYGON_AMOY_POX10": ("POLYGON", "AMOY_POX10"),
}

# What the SDK derives from each member: its session tag, the SDK registry
# tag of its trustedzone, whether a CAS provisions its securelock and whether
# it is an -unsafe variant.
DERIVED = {
    "BLOXBERG_MAINNET": ("mainnet", "bloxberg", True, False),
    "BLOXBERG_TESTNET": ("testnet", "bloxberg_testnet", True, False),
    "BLOXBERG_TESTNET_UNSAFE": ("testnet_unsafe", "bloxberg_testnet_unsafe", False, True),
    "POLYGON_MAINNET": ("mainnet", "polygon", True, False),
    "POLYGON_AMOY": ("testnet", "amoy", True, False),
    "POLYGON_AMOY_UNSAFE": ("testnet_unsafe", "amoy_unsafe", False, True),
    "IOTEX_TESTNET": ("testnet", "iotex_testnet", False, False),
    "ETHEREUM_SEPOLIA": ("testnet", "ethereum_sepolia", True, False),
    "ETHEREUM_SEPOLIA_UNSAFE": ("testnet_unsafe", "ethereum_sepolia_unsafe", False, True),
    "LITVM_LITEFORGE": ("testnet", "litvm_liteforge", True, False),
    "LITVM_LITEFORGE_UNSAFE": ("testnet_unsafe", "litvm_liteforge_unsafe", False, True),
    "BLOXBERG_TESTNET_POX10": ("bloxberg_testnet_pox10", "bloxberg_testnet_pox10", True, False),
    "POLYGON_AMOY_POX10": ("polygon_amoy_pox10", "polygon_amoy_pox10", True, False),
}

EVERY_MEMBER = pytest.mark.parametrize("name", list(MEMBERS))


def test_the_members_in_the_order_ecld_init_lists_them():
    assert [member.name for member in BlockchainNetworks] == list(MEMBERS)
    assert list(BlockchainNetworks.__members__) == list(MEMBERS), "a member with another's values would be an alias"
    assert BlockchainNetworks.get_display_options() == [values[0] for values in MEMBERS.values()]


def test_ecld_inits_default_is_the_second_of_its_list():
    # commands/init.py names "Bloxberg Testnet" as the default network.
    assert BlockchainNetworks.get_display_options()[1] == "Bloxberg Testnet"


def test_the_tables_of_this_file_name_every_member():
    assert set(DERIVED) == set(RUNNER_NETWORKS) == set(ESR_CONTRACT_ADDRESSES) == set(MEMBERS)


@EVERY_MEMBER
def test_every_value_of_the_member(name):
    member = BlockchainNetworks[name]
    template = member.template_image["Pynithy"]
    assert (member.display_name, member.network, member.network_type, member.protocol_contract_address,
            member.image_registry_contract_address, member.rpc_url, member.chain_id, member.is_eip1559,
            member.gas_price, member.max_fee_per_gas, member.max_priority_fee_per_gas,
            template.trusted_zone_image) == MEMBERS[name]
    assert list(member.template_image) == ["Pynithy"]
    assert (template.docker_repo_url, template.docker_login, template.docker_password,
            template.base_image_tag) == (DOCKER_REPO_URL, "", "", BASE_IMAGE_TAG)


@EVERY_MEMBER
def test_the_member_is_found_by_its_name_and_by_its_display_name(name):
    member = BlockchainNetworks[name]
    assert BlockchainNetworks.get_details_by_enum_name(name) is member
    assert BlockchainNetworks.get_network_details(MEMBERS[name][0]) is member
    assert BlockchainNetworks.get_enum_name(MEMBERS[name][0]) == name


def test_the_side_maps():
    assert enums.ESR_CONTRACT_ADDRESSES == ESR_CONTRACT_ADDRESSES
    assert enums.VALIDATOR_REGISTRY_ADDRESSES == VALIDATOR_REGISTRY_ADDRESSES
    assert enums.SESSION_REGISTRY_ADDRESSES == SESSION_REGISTRY_ADDRESSES
    assert enums.UNSAFE_NETWORKS == UNSAFE_NETWORKS
    assert enums.TRUSTEDZONE_TAGS == TRUSTEDZONE_TAGS
    assert enums.SESSION_TAGS == SESSION_TAGS
    assert enums.RUNNER_NETWORKS == RUNNER_NETWORKS


@EVERY_MEMBER
def test_what_the_sdk_derives_from_the_member(name):
    member = BlockchainNetworks[name]
    session_tag, trustedzone_tag, cas_provisioned, unsafe = DERIVED[name]
    assert member.session_tag == session_tag
    assert member.trustedzone_tag == trustedzone_tag
    assert member.cas_provisioned is cas_provisioned
    assert member.is_unsafe is unsafe
    assert member.runner_network == RUNNER_NETWORKS[name]
    assert (member.cas_variant.name if unsafe else member.cas_variant) == UNSAFE_NETWORKS.get(name)
    assert member.securelock_name("demo") == ("demo-unsafe" if unsafe else "demo")
    assert BlockchainNetworks.get_esr_contract_address(name) == ESR_CONTRACT_ADDRESSES[name]
    assert BlockchainNetworks.get_validator_registry_address(name) == VALIDATOR_REGISTRY_ADDRESSES.get(name, "")
    assert BlockchainNetworks.get_session_registry_address(name) == SESSION_REGISTRY_ADDRESSES.get(name, "")


def test_the_session_tags_differ_among_the_networks_scontains_cas_provisions():
    """The mainnets share the tag "mainnet"; each testnet PoX 1.0 network has its word."""
    tags = [member.session_tag for member in BlockchainNetworks if member.network_type == "mainnet"]
    assert tags == ["mainnet", "mainnet", "bloxberg_testnet_pox10", "polygon_amoy_pox10"]


def test_the_dapp_types():
    assert [kind.value for kind in dAppTypes] == ["Pynithy", "Custom"]


@EVERY_MEMBER
def test_the_runner_holds_the_members_network(name):
    """The entry of the runner's table the member maps to has the member's
    chain, PoX, image registry and trustedzone, and the Enclave State
    Registry the SDK names, or none where the SDK names none."""
    from ethernity_cloud_runner_py.contract.operation.esrContract import ESRContract
    from ethernity_cloud_runner_py.enums import ECNetwork
    member = BlockchainNetworks[name]
    network_name, network_type = member.runner_network
    entry = getattr(getattr(ECNetwork, network_name), network_type)
    assert entry.CHAIN_ID == member.chain_id
    assert entry.PROTOCOL_ADDRESS == member.protocol_contract_address
    assert entry.IMAGE_REGISTRY_CONTRACT_ADDRESS == member.image_registry_contract_address
    assert entry.EIP1559 is member.is_eip1559
    assert entry.TRUSTEDZONE_IMAGE == member.template_image["Pynithy"].trusted_zone_image
    esr = BlockchainNetworks.get_esr_contract_address(name)
    if esr:
        assert ESRContract.registry_address_for(network_name, network_type) == esr
    else:
        with pytest.raises(ValueError):
            ESRContract.registry_address_for(network_name, network_type)
