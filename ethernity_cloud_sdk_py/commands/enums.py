from enum import Enum
from dataclasses import dataclass
from typing import Optional

@dataclass
class TemplateConfig:
    trusted_zone_image: Optional[str] = None
    docker_repo_url: Optional[str] = None
    docker_login: Optional[str] = None
    docker_password: Optional[str] = None
    base_image_tag: Optional[str] = None
    

class dAppTypes(Enum):
    PYNITHY = "Pynithy"
    CUSTOM = "Custom"

class BlockchainNetworks(Enum):
    BLOXBERG_MAINNET = (
        "Bloxberg Mainnet", # Network display name
        "bloxberg", # Network short name
        "mainnet", # Network type
        "0x549A6E06BB2084100148D50F51CF77a3436C3Ae7", # protocol contract address
        "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31", # Image Registry Contract Address
        "https://core.bloxberg.org",
        8995,  # Example Chain ID
        False, # EIP-1559 SUPPORT
        0.002,  # Gas Price in Gwei for Legacy Mode
        0.002,  # maxFeePerGas in Gwei for EIP-1559
        0.0001,    # maxPriorityFeePerGas in Gwei for EIP-1559
        {  # template_images
            dAppTypes.PYNITHY.value: TemplateConfig(
                trusted_zone_image="etny-pynithy",
                docker_repo_url="registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/etny-pynithy/python3.10.5-alpine3.15-scone5.8-pre-release",
                docker_login="",
                docker_password="",
                base_image_tag="latest"
            ),
        }        
    )
    BLOXBERG_TESTNET = (
        "Bloxberg Testnet", # Network display name
        "bloxberg", # Network short name
        "testnet", # Network type
        "0x02882F03097fE8cD31afbdFbB5D72a498B41112c", # protocol contract address
        "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31", # Image Registry Contract Address
        "https://core.bloxberg.org",
        8995,  # Example Chain ID
        False, # EIP 1559 SUPPORT
        0.002,  # Example Gas Price in Gwei
        0.002,  # maxFeePerGas in Gwei
        0.0001,    # maxPriorityFeePerGas in Gwei
        {  # template_images
            dAppTypes.PYNITHY.value: TemplateConfig(
                trusted_zone_image="etny-pynithy-testnet",
                docker_repo_url="registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/etny-pynithy-testnet/python3.10.5-alpine3.15-scone5.8-pre-release",
                docker_login="",
                docker_password="",
                base_image_tag="latest"
            ),
        }    
    )
    POLYGON_MAINNET = (
        "Polygon Mainnet", # Network display name
        "polygon", # Network short name
        "mainnet", # Network type
        "0x439945BE73fD86fcC172179021991E96Beff3Cc4", # protocol contract address
        "0x689f3806874d3c8A973f419a4eB24e6fBA7E830F", # Image Registry Contract Address
        "https://polygon-rpc.com",
        137,  # Chain ID for Polygon Mainnet
        True, # EIP 1559 SUPPORT
        200,  # Gas Price in Gwei
        200,  # maxFeePerGas in Gwei
        32,  # maxPriorityFeePerGas in Gwei
        {  # template_images
            dAppTypes.PYNITHY.value: TemplateConfig(
                trusted_zone_image="ecld-pynithy",
                docker_repo_url="registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/ecld-pynithy/python3.10.5-alpine3.15-scone5.8-pre-release",
                docker_login="",
                docker_password="",
                base_image_tag="latest"
            ),
        }  
    )
    POLYGON_AMOY = (
        "Polygon Amoy", # Network display name
        "polygon", # Network short name
        "testnet", # Network type
        "0x1579b37C5a69ae02dDd23263A2b1318DE66a27C3", # protocol contract address
        "0xeFA33c3976f31961285Ae4f5D10188616C912728", # Image Registry Contract Address
        "https://rpc.ankr.com/polygon_amoy",
        80002,  # Chain ID for Polygon Mumbai Testnet
        True, # EIP 1559 SUPPORT
        100,  # Gas Price in Gwei
        64,  # maxFeePerGas in Gwei
        32,  # maxPriorityFeePerGas in Gwei
        {  # template_images
            dAppTypes.PYNITHY.value: TemplateConfig(
                trusted_zone_image="ecld-pynithy-amoy",
                docker_repo_url="registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/ecld-pynithy-amoy/python3.10.5-alpine3.15-scone5.8-pre-release",
                docker_login="",
                docker_password="",
                base_image_tag="latest"
            ),
        }  
    )
    IOTEX_TESTNET = (
        "IoTeX Testnet", # Network display name
        "iotex", # Network short name
        "testnet", # Network type
        "0xD56385A97413Ed80E28B1b54A193b98F2C49c975", # protocol contract address
        "0xa7467A6391816be9367a1cC52E0ef0c15FfE3cCC", # Image Registry Contract Address
        "https://babel-api.testnet.iotex.io",
        4690,  # Chain ID for IoTeX Testnet
        True, # EIP 1559 SUPPORT
        5,   # Gas Price in Gwei
        1500,  # maxFeePerGas in Gwei
        1,    # maxPriorityFeePerGas in Gwei
        {  # template_images
            dAppTypes.PYNITHY.value: TemplateConfig(
                trusted_zone_image="ecld-pynithy-iotex-testnet",
                docker_repo_url="registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/ecld-pynithy-iotex-testnet/python3.10.5-alpine3.15-scone5.8-pre-release",
                docker_login="",
                docker_password="",
                base_image_tag="latest"
            ),
        }
    )

    ETHEREUM_SEPOLIA = (
        "Ethereum Sepolia", # Network display name
        "ethereum", # Network short name
        "testnet", # Network type
        "0x29D3eC870565B6A1510232bd950A8Bc8336f0EB2", # protocol contract address
        "0x55e0ad455Be85162b71a790f00Fc305680E3CE53", # Image Registry Contract Address
        "https://ethereum-sepolia-rpc.publicnode.com",
        11155111,  # Chain ID for Ethreum Sepolia
        True, # EIP 1559 SUPPORT
        5,   # Gas Price in Gwei
        15,  # maxFeePerGas in Gwei
        1,    # maxPriorityFeePerGas in Gwei
        {  # template_images
            dAppTypes.PYNITHY.value: TemplateConfig(
                trusted_zone_image="ecld-pynithy-ethereum-sepolia",
                docker_repo_url="registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/ecld-pynithy-ethereum-sepolia/python3.10.5-alpine3.15-scone5.8-pre-release",
                docker_login="",
                docker_password="",
                base_image_tag="latest"
            ),
        }
    )

    def __init__(
        self,
        display_name,
        network,
        network_type,
        protocol_contract_address,
        image_registry_contract_address,
        rpc_url,
        chain_id,
        is_eip1559,
        gasprice,
        maxfeepergas,
        maxpriorityfeepergas,
        template_images
    ):
        self.display_name = display_name
        self.network = network
        self.network_type = network_type
        self.protocol_contract_address = protocol_contract_address
        self.image_registry_contract_address = image_registry_contract_address
        self.rpc_url = rpc_url
        self.chain_id = chain_id
        self.is_eip1559 = is_eip1559
        self.gas_price = gasprice
        self.max_fee_per_gas = maxfeepergas
        self.max_priority_fee_per_gas = maxpriorityfeepergas
        self.template_image = template_images

    @classmethod
    def get_display_options(cls):
        """
        Returns a list of display names for all enum members.
        """
        return [member.display_name for member in cls]

    @classmethod
    def get_network_details(cls, display_name):
        """
        Retrieves all internal values associated with a given display name.
        
        :param display_name: The display name of the blockchain network.
        :return: An instance of BlockchainNetwork or None if not found.
        """
        for member in cls:
            if member.display_name == display_name:
                return member
        return None  # Or raise an exception if preferred

    @classmethod
    def get_enum_name(cls, display_name):
        """
        ReturnsEnum member names for the requested display name.
        :param display_name: The display name of the blockchain network.
        """
        print("Available Blockchain Network Enums:")
        for member in cls:
            if member.display_name == display_name:
                return member.name

    @classmethod
    def get_details_by_enum_name(cls, enum_name):
        """
        Retrieves all internal values associated with a given Enum member name.

        :param enum_name: The name of the Enum member (e.g., "BLOXBERG_MAINNET").
        :return: An instance of BlockchainNetwork or None if not found.
        """
        try:
            member = cls[enum_name]
            return member
        except KeyError:
            print(f"Enum member '{enum_name}' does not exist.")
            return None