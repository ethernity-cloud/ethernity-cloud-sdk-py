import os
import sys
import json
import pathlib
import time
from dotenv import load_dotenv
from eth_utils.address import to_checksum_address
from web3 import Web3
#from web3.middleware.geth_poa import geth_poa_middleware
from web3.middleware import ExtraDataToPOAMiddleware
from eth_account import Account

from pathlib import Path
from ethernity_cloud_sdk_py.commands.config import Config, config
from ethernity_cloud_sdk_py.commands.spinner import Spinner

config = Config(Path(".config.json").resolve())
config.load()

class ImageRegistry:
    def __init__(self, private_key):
        try:
            self.private_key = private_key
            self.blockchain_network = config.read("BLOCKCHAIN_NETWORK")
            self.project_name = config.read("PROJECT_NAME")
            self.enclave_name_securelock = self.project_name
            self.securelock_session = config.read("SECURELOCK_SESSION")
            self.securelock_version = config.read("VERSION")
            self.enclave_name_trustedzone = config.read("TRUSTED_ZONE_IMAGE")
            self.trustedzone_version = "v3"

            if "Bloxberg" in self.blockchain_network:
                self.image_registry_address = "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31"
                self.network_rpc = "https://core.bloxberg.org"
                self.chain_id = 8995
                self.gas = 9000000
                self.gas_price = Web3.to_wei(1, "mwei")  # 1 Mwei
            elif "Polygon" in self.blockchain_network:
                if "Mainnet" in self.blockchain_network:
                    self.network_rpc = "https://polygon-rpc.com"
                    self.image_registry_address = "0x689f3806874d3c8A973f419a4eB24e6fBA7E830F"
                    self.chain_id = 137
                    self.gas = 20000000
                    self.gas_price = Web3.to_wei(40500500010, "wei")
                else:
                    self.network_rpc = "https://rpc-amoy.polygon.technology"
                    self.image_registry_address = "0xF7F4eEb3d9a64387F4AcEb6d521b948E6E2fB049"
                    self.chain_id = 80001
                    self.gas = 20000000
                    self.gas_price = Web3.to_wei(1300000010, "wei")
            else:
                # Default to Bloxberg Testnet if no matching network
                self.image_registry_address = "0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31"
                self.network_rpc = "https://core.bloxberg.org"
                self.chain_id = 8995
                self.gas = 9000000
                self.gas_price = Web3.to_wei(1, "mwei")  # 1 Mwei

            self.image_registry_abi = self.read_contract_abi("image_registry.abi")
            self.provider = self.new_provider(self.network_rpc)

            
            # # Inject middleware if needed
            # if "Bloxberg" in BLOCKCHAIN_NETWORK or "Polygon" in BLOCKCHAIN_NETWORK:
            #     self.provider.middleware_onion.inject(geth_poa_middleware, layer=0)


            self.acct = Account().from_key(self.private_key)
            self.provider.eth.default_account = self.acct.address

            self.image_registry_contract = self.provider.eth.contract(
                address=to_checksum_address(self.image_registry_address),
                abi=self.image_registry_abi,
            )
        except Exception as e:
            raise Exception("Error initializing image registry: " + str(e))

        
    def check_balance(self):
        try:
            balance = self.provider.eth.get_balance(self.acct.address)
            return Web3.from_wei(balance, "ether")
        except Exception as e:
            print(e)
            return 0

    def check_image_permissions(self):
        try:
            image_hash = self._get_latest_image_version_public_key(
                self.project_name, self.securelock_version
            )[0]

        except Exception as e:
            print(f"Error recovering public key for enclave {self.project_name} version {self.securelock_version}: {e}")
            exit(1)

        if not image_hash:
            return f"\t\u2714  Project is available on the {self.blockchain_network}"
        
            
        try:
            image_owner = self.get_image_details(image_hash).owner
        except Exception as e:
            print(f"Error recovering image owner for image hash {image_hash}: {e}")
            exit(1)
    
        if image_owner.lower() != self.acct.address.lower():
            print(
                f"\t\u2718  Enclave '{project_name}' is owned by '{image_owner}'.\nYou are not the account holder of the image.\nPlease change the project name and try again.\n"
            )
            exit(1)
            
        return f"\t\u2714  Project ownership verified on {self.blockchain_network}"
        


    def new_provider(self, url: str) -> Web3:
        w3 = Web3(Web3.HTTPProvider(url))
        #_w3.enable_unstable_package_management_api()
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        return w3

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
            tx_hash = self.provider.eth.send_raw_transaction(signed_txn.raw_transaction)
            print(f"Transaction sent: {tx_hash.hex()}")

            receipt = self.provider.eth.wait_for_transaction_receipt(tx_hash)
            if receipt.status == 1:
                print("Adding trusted zone cert transaction was successful!")
            else:
                print("Adding trusted zone cert transaction was UNSUCCESSFUL!")
                exit(1)
        except Exception as e:
            print(f"An error occurred while sending transaction: {e}")

    def build_transaction_add_image(
        self,
        cert_content,
        ipfs_hash,
        image_name,
        version,
        docker_compose_hash,
        enclave_name_securelock,
        fee,
    ):
        #print("Adding secure lock image cert to image registry")
        try:
            if "Polygon" in self.blockchain_network:
                print("Polygon network")
                nonce = self.provider.eth.get_transaction_count(
                    self.acct.address, "pending"
                )
                gas_price = self.provider.eth.gas_price
                gas_price = int(gas_price * 1.1)  # Increase gas price by 10%
            else:
                nonce = self.provider.eth.get_transaction_count(self.acct.address)
                gas_price = (
                    self.gas_price if self.gas_price != 1 else self.provider.to_wei(1, "mwei")
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
                    "gas": self.gas,
                    "gasPrice": gas_price,
                    "chainId": self.chain_id,
                    "from": self.acct.address,
                }
            )

            signed_txn = self.provider.eth.account.sign_transaction(
                txn, private_key=self.private_key
            )
            return signed_txn
        except Exception as e:
            print (f"Failed to prepare and sign transaction: {e}")
            return False
        
    def process_transaction(self, txn):
        while True:
            try:
                tx_hash = self.provider.eth.send_raw_transaction(txn.raw_transaction)
            except Exception as e:
                pass

            try:
                receipt = self.provider.eth.wait_for_transaction_receipt(tx_hash)
                if receipt.status == 1:
                    return True
            except Exception as e:
                print(f"\n\t\tUnable to register secure lock enclave: {e}\nRetrying...\n")
                time.sleep(1)

                
    def get_image_public_key(self, ipfs_hash):
        try:
            print("Getting image cert from image registry")
            public_key = self.image_registry_contract.functions.getImageCertPublicKey(
                ipfs_hash
            ).call()
            return public_key
        except Exception as e:
            print(f"Error retrieving image public key certificate: {str(e)}")
            return None

    def get_image_details(self, ipfs_hash):
        try:
            result = self.image_registry_contract.functions.imageDetails(
                ipfs_hash
            ).call()

            details = lambda:None
            details.owner = result[0]
            details.name = result[10]
            details.ipfs_hash = result[1]
            details.public_key = result[8]
            details.docker_compose_hash = result[9]

            return details
        except Exception as e:
            # print(f"Error: {str(e)}")
            return None

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
        
    def get_trusted_zone_public_key(self):
        public = self._get_latest_image_version_public_key(
            self.enclave_name_trustedzone, self.trustedzone_version
        )[1]
        return public


    def register_securelock_image(self, public_key):
        spinner = Spinner()
        config.load()
        try:
            ipfs_hash = config.read("IPFS_HASH")
            ipfs_docker_compose_hash = config.read("IPFS_DOCKER_COMPOSE_HASH")
            self.securelock_session = config.read("SECURELOCK_SESSION")
            fee = config.read("DEVELOPER_FEE")

            txn = spinner.spin_till_done(
                "Building transaction for securelock enclave registration",
                self.build_transaction_add_image,
                public_key,
                ipfs_hash,
                self.enclave_name_securelock,
                str(self.securelock_version),
                ipfs_docker_compose_hash,
                self.securelock_session,
                fee,
            )

            result = spinner.spin_till_done(
                f"Processing transaction 0x{txn.hash.hex()}",
                self.process_transaction,
                txn
            )

            return result
        except Exception as e:
            print(f"Unable to create secure lock image: {e}")
            return False
        