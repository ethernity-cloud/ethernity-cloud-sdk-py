import os
import sys
import json
import pathlib
from dotenv import load_dotenv
from eth_utils.address import to_checksum_address
from web3 import Web3
from web3.middleware.geth_poa import geth_poa_middleware
from eth_account import Account

current_dir = os.getcwd()
dotenv_path = os.path.join(current_dir, ".env")
# Load environment variables from .env file
load_dotenv(dotenv_path=dotenv_path)

# Environment variables
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
BLOCKCHAIN_NETWORK = os.getenv("BLOCKCHAIN_NETWORK", "Bloxberg_Testnet")

# Default values
NETWORK_RPC = "https://core.bloxberg.org"
IMAGE_REGISTRY_ADDRESS = (
    "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31"  # Bloxberg testnet
)
CHAIN_ID = 8995
GAS = 9000000
GAS_PRICE = 1


def set_vars(network=""):
    global NETWORK_RPC, IMAGE_REGISTRY_ADDRESS, CHAIN_ID, GAS, GAS_PRICE, BLOCKCHAIN_NETWORK
    if network:
        BLOCKCHAIN_NETWORK = network
    if "Bloxberg" in BLOCKCHAIN_NETWORK:
        IMAGE_REGISTRY_ADDRESS = "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31"
        NETWORK_RPC = "https://core.bloxberg.org"
        CHAIN_ID = 8995
        GAS = 9000000
        GAS_PRICE = Web3.to_wei(1, "mwei")  # 1 Mwei
    elif "Polygon" in BLOCKCHAIN_NETWORK:
        if "Mainnet" in BLOCKCHAIN_NETWORK:
            NETWORK_RPC = "https://polygon-rpc.com"
            IMAGE_REGISTRY_ADDRESS = "0x689f3806874d3c8A973f419a4eB24e6fBA7E830F"
            CHAIN_ID = 137
            GAS = 20000000
            GAS_PRICE = Web3.to_wei(40500500010, "wei")
        else:
            NETWORK_RPC = "https://rpc-amoy.polygon.technology"
            IMAGE_REGISTRY_ADDRESS = "0xF7F4eEb3d9a64387F4AcEb6d521b948E6E2fB049"
            CHAIN_ID = 80001
            GAS = 20000000
            GAS_PRICE = Web3.to_wei(1300000010, "wei")
    else:
        # Default to Bloxberg Testnet if no matching network
        IMAGE_REGISTRY_ADDRESS = "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31"
        NETWORK_RPC = "https://core.bloxberg.org"
        CHAIN_ID = 8995
        GAS = 9000000
        GAS_PRICE = Web3.to_wei(1, "mwei")  # 1 Mwei


# Set variables based on the current network
set_vars()


def is_string_private_key(private_key):
    try:
        key = private_key
        if not key.startswith("0x"):
            key = f"0x{private_key}"
        Account().from_key(key)
        return "OK"
    except Exception as e:
        return str(e)


def check_account_balance():
    try:
        w3 = Web3(Web3.HTTPProvider(NETWORK_RPC))
        if PRIVATE_KEY:
            account = Account().from_key(PRIVATE_KEY)
        else:
            print("Error: PRIVATE_KEY is not set.")
            return 0
        balance = w3.eth.get_balance(account.address)
        return Web3.from_wei(balance, "ether")
    except Exception as e:
        print(e)
        return 0


class ImageRegistry:
    def __init__(self):
        try:
            self.image_registry_abi = self.read_contract_abi("image_registry.abi")
            self.image_registry_address = IMAGE_REGISTRY_ADDRESS
            print(f"imageRegistryAddress: {self.image_registry_address}")
            # self.provider = Web3(Web3.HTTPProvider(NETWORK_RPC))
            # print(f"Network RPC: {NETWORK_RPC}")
            self.provider = self.newProvider(NETWORK_RPC)

            # # Inject middleware if needed
            # if "Bloxberg" in BLOCKCHAIN_NETWORK or "Polygon" in BLOCKCHAIN_NETWORK:
            #     self.provider.middleware_onion.inject(geth_poa_middleware, layer=0)

            if PRIVATE_KEY:
                # print("Using private key from environment variable")
                self.acct = Account().from_key(PRIVATE_KEY)
                self.provider.eth.default_account = self.acct.address
            else:
                _private_key = Account().create().key
                self.acct = Account().from_key(_private_key)
                self.provider.eth.default_account = self.acct.address

            self.image_registry_contract = self.provider.eth.contract(
                address=to_checksum_address(self.image_registry_address),
                abi=self.image_registry_abi,
            )
        except Exception as e:
            print(e)

    def newProvider(self, url: str) -> Web3:
        _w3 = Web3(Web3.HTTPProvider(url))
        _w3.enable_unstable_package_management_api()
        _w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        return _w3

    def read_contract_abi(self, contract_name):
        file_path = pathlib.Path(__file__).parent / contract_name
        with open(file_path, "r") as f:
            return json.load(f)

    def add_trusted_zone_cert(
        self,
        cert_content,
        ipfs_hash,
        image_name,
        docker_compose_hash,
        enclave_name_trustedzone,
        fee,
    ):
        print("Adding trusted zone cert to image registry")
        try:
            nonce = self.provider.eth.get_transaction_count(self.acct.address)
            gas_price = GAS_PRICE if GAS_PRICE != 1 else self.provider.to_wei(1, "mwei")
            txn = self.image_registry_contract.functions.addTrustedZoneImage(
                ipfs_hash,
                cert_content,
                "v3",
                image_name,
                docker_compose_hash,
                enclave_name_trustedzone,
                int(fee),
            ).build_transaction(
                {
                    "nonce": nonce,
                    "gas": GAS,
                    "gasPrice": gas_price,
                    "chainId": CHAIN_ID,
                    "from": self.acct.address,
                }
            )

            signed_txn = self.provider.eth.account.sign_transaction(
                txn, private_key=PRIVATE_KEY
            )
            tx_hash = self.provider.eth.send_raw_transaction(signed_txn.rawTransaction)
            print(f"Transaction sent: {tx_hash.hex()}")

            receipt = self.provider.eth.wait_for_transaction_receipt(tx_hash)
            if receipt.status == 1:
                print("Adding trusted zone cert transaction was successful!")
            else:
                print("Adding trusted zone cert transaction was UNSUCCESSFUL!")
        except Exception as e:
            print(f"An error occurred while sending transaction: {e}")

    def add_secure_lock_image_cert(
        self,
        cert_content,
        ipfs_hash,
        image_name,
        version,
        docker_compose_hash,
        enclave_name_securelock,
        fee,
    ):
        try:
            print("Adding secure lock image cert to image registry")

            if "Polygon" in BLOCKCHAIN_NETWORK:
                print("Polygon network")
                nonce = self.provider.eth.get_transaction_count(
                    self.acct.address, "pending"
                )
                gas_price = self.provider.eth.gas_price
                gas_price = int(gas_price * 1.1)  # Increase gas price by 10%
            else:
                nonce = self.provider.eth.get_transaction_count(self.acct.address)
                gas_price = (
                    GAS_PRICE if GAS_PRICE != 1 else self.provider.to_wei(1, "mwei")
                )

            txn = self.image_registry_contract.functions.addImage(
                ipfs_hash,
                cert_content,
                version,
                image_name,
                docker_compose_hash,
                enclave_name_securelock,
                int(fee),
            ).build_transaction(
                {
                    "nonce": nonce,
                    "gas": GAS,
                    "gasPrice": gas_price,
                    "chainId": CHAIN_ID,
                    "from": self.acct.address,
                }
            )

            signed_txn = self.provider.eth.account.sign_transaction(
                txn, private_key=PRIVATE_KEY
            )
            tx_hash = self.provider.eth.send_raw_transaction(signed_txn.rawTransaction)
            print(f"Transaction sent: {tx_hash.hex()}")

            receipt = self.provider.eth.wait_for_transaction_receipt(tx_hash)
            if receipt.status == 1:
                print("Adding secure lock image cert transaction was successful!")
            else:
                print("Image certificates already exist for this image!")
        except Exception as e:
            # Uncomment the next line to see the actual error
            print(f"Error: {str(e)}")
            print("Image certificates already exist for this image!")

    def get_image_public_key_cert(self, ipfs_hash):
        try:
            print("Getting image cert from image registry")
            cert = self.image_registry_contract.functions.getImageCertPublicKey(
                ipfs_hash
            ).call()
            print("Image Public Key Certificate:", cert)
            return cert
        except Exception as e:
            print(f"Error retrieving image public key certificate: {str(e)}")
            return None

    def get_image_details(self, ipfs_hash):
        try:
            details = self.image_registry_contract.functions.imageDetails(
                ipfs_hash
            ).call()
            return details
        except Exception as e:
            # Uncomment to see the actual error
            print(f"Error: {str(e)}")
            return ("", "", "")

    def _get_latest_image_version_public_key(self, project_name, version):
        try:
            public_key_tuple = (
                self.image_registry_contract.functions.getLatestImageVersionPublicKey(
                    project_name, version
                ).call()
            )
            # The function returns a tuple, extract the fields as needed
            return public_key_tuple
        except Exception as e:
            # Uncomment to see the actual error
            # print(f"Error: {str(e)}")
            return ("", "", "")


def main(
    network_name="", project_name="", version_arg="", private_key_arg="", action=""
):
    global current_dir
    current_dir = os.getcwd()
    dotenv_path = os.path.join(current_dir, ".env")
    # Load environment variables from .env file
    load_dotenv(dotenv_path=dotenv_path)
    global PRIVATE_KEY, BLOCKCHAIN_NETWORK, NETWORK_RPC, IMAGE_REGISTRY_ADDRESS, CHAIN_ID, GAS, GAS_PRICE
    # Environment variables
    PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
    BLOCKCHAIN_NETWORK = os.getenv("BLOCKCHAIN_NETWORK", "Bloxberg_Testnet")

    # Default values
    NETWORK_RPC = "https://core.bloxberg.org"
    IMAGE_REGISTRY_ADDRESS = (
        "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31"  # Bloxberg testnet
    )
    CHAIN_ID = 8995
    GAS = 9000000
    GAS_PRICE = 1

    if action == "validateAddress":
        res = "OK"
        if private_key_arg:
            res = is_string_private_key(private_key_arg)
        print(res)
        # sys.exit(0)
        return res

    if network_name and project_name:
        set_vars(network_name)
    else:
        set_vars()

    if action == "checkBalance":
        balance = check_account_balance()
        print(f"{balance} gas")
        return str(balance)
        # sys.exit(0)

    if action == "getTrustedZoneCert":
        image_registry = ImageRegistry()
        public = image_registry._get_latest_image_version_public_key(
            project_name, version_arg
        )[1]
        print(public)
        return public
        # sys.exit(0)

    image_registry = ImageRegistry()

    if action == "registerSecureLockImage":
        secure_lock = ""
        with open("./registry/certificate.securelock.crt", "r") as f:
            secure_lock = f.read()
        ipfs_hash = os.getenv("IPFS_HASH", "")
        print(f"ipfsHash: {ipfs_hash}")
        ipfs_docker_compose_hash = os.getenv("IPFS_DOCKER_COMPOSE_HASH", "")
        print(f"ipfsDockerComposeHash: {ipfs_docker_compose_hash}")
        image_name = os.getenv("PROJECT_NAME", "")
        print(f"imageName: {image_name}")
        version = os.getenv("VERSION", "")
        enclave_name_securelock = os.getenv("ENCLAVE_NAME_SECURELOCK", "")
        print(f"enclaveNameSecureLock: {enclave_name_securelock}")
        fee = os.getenv("DEVELOPER_FEE", "0")
        print(f"fee: {fee}")
        image_registry.add_secure_lock_image_cert(
            secure_lock,
            ipfs_hash,
            image_name,
            version,
            ipfs_docker_compose_hash,
            enclave_name_securelock,
            fee,
        )
        # sys.exit(0)
        return "OK"
    if action == "getImagePublicKey":
        ipfs_hash = os.getenv("IPFS_HASH", "")
        image_registry.get_image_public_key_cert(ipfs_hash)
        # sys.exit(0)
        return

    print(f"Checking image: '{project_name}' on the {BLOCKCHAIN_NETWORK} blockchain...")
    # print(f"Arguments: {args}")
    try:
        image_hash = image_registry._get_latest_image_version_public_key(
            project_name, version_arg if version_arg else "v3"
        )[0]
    except Exception as e:
        image_hash = ""
        # print(f"Error: {str(e)}")

    # print(f"Image hash: {image_hash}")
    if not image_hash:
        print(
            f"Image: '{project_name}' is available on the {BLOCKCHAIN_NETWORK} blockchain."
        )
        return "OK"
    print(f"Image hash: {image_hash}")
    image_owner = image_registry.get_image_details(image_hash)[0]
    if private_key_arg:
        if is_string_private_key(private_key_arg) == "OK":
            account = Account().from_key(private_key_arg)
            if image_owner.lower() != account.address.lower():
                print(
                    f"!!! Image: '{project_name}' is owned by '{image_owner}'.\nYou are not the account holder of the image.\nPlease change the project name and try again.\n"
                )
                return "You are not the account holder of the image."
    if image_owner:
        print(
            f"Image: '{project_name}' is owned by '{image_owner}'.\nIf you are not the account holder, you will not be able to publish your project with the current name. Please change the project name and try again.\n"
        )
        # sys.exit(0)
        return "You are not the account holder of the image."

    print(
        f"Image: '{project_name}' is available on the {BLOCKCHAIN_NETWORK} blockchain."
    )


if __name__ == "__main__":
    try:
        args = sys.argv[1:]
        if len(args) >= 5:
            network_name, project_name, version_arg, private_key_arg, action = args[:5]
        elif len(args) == 4:
            network_name, project_name, version_arg, private_key_arg = args[:4]
            action = ""
        elif len(args) == 3:
            network_name, project_name, version_arg = args[:3]
            action = ""
            private_key_arg = ""
        elif len(args) == 2:
            network_name, project_name = args[:2]
            action = ""
            version_arg = ""
            private_key_arg = ""
        elif len(args) == 1:
            action = ""
            network_name = args[0]
            project_name = ""
            version_arg = ""
            private_key_arg = ""

        #     network_name = ""
        #     project_name = ""
        #     version_arg = ""
        #     private_key_arg = ""
        #     action = ""
        main(network_name, project_name, version_arg, private_key_arg, action)
    except Exception as e:
        # Uncomment to see the actual error
        print(f"Error: {str(e)}")
        # sys.exit(0)
