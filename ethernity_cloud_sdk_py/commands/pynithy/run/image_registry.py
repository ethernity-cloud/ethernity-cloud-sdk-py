import os
import sys
import json
import pathlib
import time
from dotenv import load_dotenv
from eth_utils.address import to_checksum_address
from web3 import Web3
#from web3.middleware.geth_poa import geth_poa_middleware
from web3.exceptions import ContractLogicError
from web3.middleware import ExtraDataToPOAMiddleware
from eth_account import Account

from pathlib import Path
from ethernity_cloud_sdk_py.commands.config import Config, config
from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks
from ethernity_cloud_sdk_py.commands.spinner import Spinner

config = Config(Path(".config.json").resolve())
config.load()

class ImageRegistry:
    def __init__(self):
        try:

            self.blockchain_network = config.read("BLOCKCHAIN_NETWORK")
            self.project_name = config.read("PROJECT_NAME")
            # The name the securelock is registered under: the project name,
            # <project>-unsafe on an -unsafe network (enums.securelock_name).
            self.enclave_name_securelock = BlockchainNetworks.get_details_by_enum_name(
                self.blockchain_network).securelock_name(self.project_name)
            self.securelock_session = config.read("SECURELOCK_SESSION")
            # This is the PROTOCOL version used as the Image Registry key (what
            # the runner queries with getLatestImageVersionPublicKey(name, "v3")),
            # NOT the enclave/template VERSION. It must be "v3" to match the read
            # side (check_image_permissions) and the runner; config VERSION (e.g.
            # "21") wrote/read the "latest" pointer under the wrong key.
            self.securelock_version = "v3"
            self.enclave_name_trustedzone = config.read("TRUSTED_ZONE_IMAGE")
            self.trustedzone_version = "v3"
            self.blockchain_config = BlockchainNetworks.get_details_by_enum_name(self.blockchain_network)
            self.image_registry_address = self.blockchain_config.image_registry_contract_address
            self.network_rpc = self.blockchain_config.rpc_url
            self.chain_id = self.blockchain_config.chain_id
            self.is_eip1559 = self.blockchain_config.is_eip1559
            self.gas_price = self.blockchain_config.gas_price
            self.max_fee_per_gas = self.blockchain_config.max_fee_per_gas
            self.max_priority_fee_per_gas = self.blockchain_config.max_priority_fee_per_gas

            self.image_registry_abi = self.read_contract_abi("image_registry.abi")
            self.provider = self.new_provider(self.network_rpc)

            
            # # Inject middleware if needed
            # if "Bloxberg" in BLOCKCHAIN_NETWORK or "Polygon" in BLOCKCHAIN_NETWORK:
            #     self.provider.middleware_onion.inject(geth_poa_middleware, layer=0)

            self.image_registry_contract = self.provider.eth.contract(
                address=to_checksum_address(self.image_registry_address),
                abi=self.image_registry_abi,
            )

        except Exception as e:
            raise Exception("Error initializing image registry: " + str(e))

    def set_private_key(self, private_key):
        self.private_key = private_key
        self.acct = Account().from_key(self.private_key)
        self.provider.eth.default_account = self.acct.address

        
    def check_balance(self):
        try:
            balance = self.provider.eth.get_balance(self.acct.address)
            return Web3.from_wei(balance, "ether")
        except Exception as e:
            print(e)
            return 0

    def get_balance_of(self, address):
        """Native-token balance of `address`, in ether units."""
        try:
            balance = self.provider.eth.get_balance(to_checksum_address(address))
            return Web3.from_wei(balance, "ether")
        except Exception as e:
            print(f"Error reading balance of {address}: {e}")
            return None

    def transfer_native(self, to_address, amount_ether):
        """Send `amount_ether` of the native token to `to_address`.

        Used only by the opt-in ESR auto-funding path (ESR RFC §5.3). Signs with
        the same developer key that already pays for image registration, so no
        new key material is introduced. Returns the tx hash on success, None on
        failure -- funding must never abort a publish that otherwise succeeded.
        """
        try:
            to_address = to_checksum_address(to_address)
            value = Web3.to_wei(str(amount_ether), "ether")
            tx = {
                "from": self.acct.address,
                "to": to_address,
                "value": value,
                "nonce": self.provider.eth.get_transaction_count(self.acct.address),
                "chainId": self.blockchain_config.chain_id,
            }
            # Gas: a plain transfer is 21000. Price via the node so this works on
            # both legacy and EIP-1559 chains without special-casing each network.
            tx["gas"] = 21000
            try:
                tx["gasPrice"] = self.provider.eth.gas_price
            except Exception:
                pass

            signed = self.provider.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.provider.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.provider.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
            if receipt and receipt.get("status") == 1:
                return self.provider.to_hex(tx_hash)
            print(f"\t✘  Funding transaction reverted (status={receipt and receipt.get('status')})")
            return None
        except Exception as e:
            print(f"\t✘  Funding transfer failed: {e}")
            return None

    def check_image_permissions(self):
        # On a registry with name owners the name's owner answers, whether or
        # not an image is certified under the name yet.
        name_owner = self.image_name_owner(self.enclave_name_securelock)
        if name_owner is not None:
            if int(name_owner, 16) == 0:
                return True
            if name_owner.lower() != self.acct.address.lower():
                print(
                    f"\t✘  Enclave '{self.enclave_name_securelock}' is owned by '{name_owner}'.\nYou are not the account holder of the image.\nPlease change the project name and try again.\n"
                )
                return False
            return f"\t✔  Project ownership verified on {self.blockchain_network}"

        try:
            image_hash = self._get_latest_image_version_public_key(
                self.enclave_name_securelock, self.securelock_version
            )

        except Exception as e:
            print(f"Error recovering public key for enclave {self.enclave_name_securelock} version {self.securelock_version}: {e}")
            return False

        if not image_hash[0] or image_hash[0] == "":
            #print(f"\t\u2714  Project is available on the {self.blockchain_network}")
            return True
            
        try:
            # image_hash is the (ipfs_hash, cert, docker_compose_hash) tuple
            # getLatestImageVersionPublicKey returns; imageDetails is keyed by
            # the ipfs hash alone. Passing the tuple raises MismatchedABI, which
            # was swallowed here and reported as an ownership failure, so every
            # republish by the rightful owner was refused.
            image_owner = self.get_image_details(image_hash[0]).owner
        except Exception as e:
            print(f"Error recovering image owner for image hash {image_hash[0]}: {e}")
            return False
    
        if image_owner.lower() != self.acct.address.lower():
            print(
                f"\t\u2718  Enclave '{self.enclave_name_securelock}' is owned by '{image_owner}'.\nYou are not the account holder of the image.\nPlease change the project name and try again.\n"
            )
            return False
            
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

    def _transaction_options(self, gas_limit):
        """The options of a transaction from the publishing wallet: its pending
        nonce and the network's fee model, with `gas_limit` named where fees are
        not quoted."""
        nonce = self.provider.eth.get_transaction_count(
            self.acct.address, "pending"
        )
        if self.blockchain_config.is_eip1559:
            latest_block = self.provider.eth.get_block("latest")

            max_fee_per_gas = int(latest_block.baseFeePerGas * 1.1) + self.provider.to_wei(self.blockchain_config.max_priority_fee_per_gas, 'gwei') # 10% increase in previous block gas price + priority fee

            if max_fee_per_gas > self.provider.to_wei(self.blockchain_config.max_fee_per_gas, 'gwei'):
                raise Exception("Network fee per gas is too high!")

            return {
                "type": 2,
                "nonce": nonce,
                "chainId": self.blockchain_config.chain_id,
                "from": self.acct.address,
                'maxFeePerGas': max_fee_per_gas,
                'maxPriorityFeePerGas': self.provider.to_wei(self.blockchain_config.max_priority_fee_per_gas, 'gwei'),
            }
        return {
            "nonce": nonce,
            "chainId": self.blockchain_config.chain_id,
            "from": self.acct.address,
            "gasPrice": self.provider.to_wei(self.blockchain_config.gas_price, 'gwei'),
            "gas": gas_limit,
        }

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
        if self.blockchain_config.network == "bloxberg":
            gasLimit = 9000000
        else:
            gasLimit = 1200000

        try:
            transaction_options = self._transaction_options(gasLimit)

            txn = self.image_registry_contract.functions.addImage(
            ipfs_hash,
            cert_content,
            version,
            image_name,
            docker_compose_hash,
            enclave_name_securelock,
            int(fee),
            ).build_transaction(transaction_options)

            signed_txn = self.provider.eth.account.sign_transaction(
                txn, private_key=self.private_key
            )
            
            return signed_txn
        except Exception as e:
            print (f"\tFailed to prepare and sign transaction: {e}")
            return False
        
    def process_transaction(self, txn):
        attempts = 0
        while True:
            attempts += 1
            try:
                tx_hash = self.provider.eth.send_raw_transaction(txn.raw_transaction)
            except Exception as e:
                # A pre-send revert surfaces here on some nodes. Propagate so the
                # caller decides, instead of continuing with an undefined tx_hash.
                raise

            try:
                receipt = self.provider.eth.wait_for_transaction_receipt(tx_hash)
                if receipt.status == 1:
                    return True
                # status == 0 => mined but reverted. Do NOT retry forever (the same
                # signed tx would revert identically); raise so the caller handles
                # it (e.g. an idempotent "already in registry" re-register).
                raise Exception(f"transaction {tx_hash.hex()} reverted (status 0)")
            except Exception as e:
                print(f"\n\t\tUnable to register secure lock enclave: {e}\n")
                # Bounded retry for transient RPC/receipt errors only.
                if attempts >= 3:
                    raise
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
        

    def get_trusted_zone_hash(self, trusted_zone_image, version):
        try:
            public_key = self.image_registry_contract.functions.getLatestTrustedZoneImageCertPublicKey(
                trusted_zone_image, version
            ).call()
            return public_key[0]
        except Exception as e:
            print(f"Error retrieving image public key certificate: {str(e)}")
            return None

    def get_trustezone_image_session(self, ipfs_hash):
        try:
            public_key = self.image_registry_contract.functions.getTrustedZoneImageSession(
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
            details.session = result[3]
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
        ipfs_hash = config.read("IPFS_HASH")
        ipfs_docker_compose_hash = config.read("IPFS_DOCKER_COMPOSE_HASH")
        self.securelock_session = config.read("SECURELOCK_SESSION")
        # The publisher's fee, in percent of each task's base price, which PoX
        # adds to what the dApp user pays and pays to the image's reward
        # address; 0 unless .config.json sets DEVELOPER_FEE (the registry
        # accepts up to 15).
        fee = int(config.read("DEVELOPER_FEE") or 0)

        # Register the SAME build under two registry version keys:
        #   "v3"           -> the moving "latest" pointer. The runner resolves
        #                     getLatestImageVersionPublicKey(name, "v3"), which
        #                     returns the last hash pushed to this channel, so
        #                     writing here makes this build the new default.
        #   <VERSION>       -> an immutable per-version entry (e.g. "22"). It
        #                     preserves this exact build forever so it can be
        #                     pinned/rolled back to via
        #                     getLatestImageVersionPublicKey(name, "22").
        # The contract stores each (name, version) channel append-only and reverts
        # if the SAME hash is re-added to the SAME channel. We pre-check each
        # channel's current latest hash and skip the write if it already points at
        # this build, so an idempotent re-publish is a no-op instead of a revert.
        enclave_version = str(config.read("VERSION"))
        version_keys = ["v3"]
        if enclave_version and enclave_version != "v3":
            version_keys.append(enclave_version)

        last_result = None
        for version_key in version_keys:
            # Skip if this exact hash is already the latest under this channel
            # (_get_latest_image_version_public_key returns (ipfsHash, cert,
            # dockerComposeHash), or ("","","") when the channel has no image yet).
            current = self._get_latest_image_version_public_key(
                self.enclave_name_securelock, version_key
            )
            if current and current[0] == ipfs_hash:
                print(f"\t✔  securelock already registered under version '{version_key}' (skipping)")
                continue

            max_retries = 3
            for attempt in range(1, max_retries + 1):
                try:
                    time.sleep(5)
                    txn = spinner.spin_till_done(
                        f"Building transaction for securelock registration (version '{version_key}')",
                        self.build_transaction_add_image,
                        public_key,
                        ipfs_hash,
                        self.enclave_name_securelock,
                        version_key,
                        ipfs_docker_compose_hash,
                        self.securelock_session,
                        fee,
                    )
                    if txn == False:
                        if attempt >= max_retries:
                            raise Exception("the addImage transaction could not be built")
                        continue

                    # Called directly: a transaction that reverts must raise
                    # here, and the spinner returns None for any exception.
                    print(f"\t   Processing transaction 0x{txn.hash.hex()}")
                    last_result = self.process_transaction(txn)
                    break  # this channel registered successfully
                except Exception as e:
                    if "already in registry" in str(e).lower():
                        print(f"\t✔  securelock already registered under version '{version_key}' (skipping)")
                        break
                    print(f"\tUnable to register securelock (version '{version_key}', attempt {attempt}/{max_retries}): {e}")
                    if attempt >= max_retries:
                        raise

        # REWARD_ADDRESS: where the image's developer fee is paid, when not the
        # publishing wallet.
        reward_address = config.read("REWARD_ADDRESS")
        if reward_address:
            self.set_reward_address(ipfs_hash, reward_address)

        return last_result

    def is_v2(self):
        """Whether the network's registry is an ECImageRegistryV2 or later,
        which records an image before its certificate exists (registerImage,
        then setImageCert). A V1 registry has no pendingImages() and reverts;
        a node that does not answer raises."""
        try:
            self.image_registry_contract.functions.pendingImages().call()
            return True
        except ContractLogicError:
            return False

    def image_name_owner(self, image_name):
        """The wallet that owns the securelock name on an ECImageRegistryV3 or
        later (the zero address while nobody does), or None on a registry
        without name owners. Only the owner publishes under the name, and a
        name and its -unsafe twin have one owner."""
        try:
            return self.image_registry_contract.functions.imageNameOwner(image_name).call()
        except ContractLogicError:
            return None

    def _send(self, txn_builder, gas_limit, label):
        """Sign and send a transaction built by `txn_builder` from the
        publishing wallet, waiting for its receipt; a transaction that
        reverts raises. The call is simulated first: a transaction sent with a
        fixed gas limit and reverted reports no reason, the simulation reports
        the registry's."""
        try:
            txn_builder.call({"from": self.acct.address})
        except ContractLogicError as e:
            raise Exception(f"{label}: the image registry refuses it ({e})")
        txn = txn_builder.build_transaction(self._transaction_options(gas_limit))
        signed = self.provider.eth.account.sign_transaction(txn, private_key=self.private_key)
        print(f"\t   {label}: transaction 0x{signed.hash.hex()}")
        return self.process_transaction(signed)

    def register_image(self, ipfs_hash, docker_compose_hash, ipfs_peer):
        """Record the securelock on a V2 registry before its certificate
        exists: name, protocol version v3, compose, session, the publisher's
        fee and the IPFS node that holds the image (`ipfs_peer`, a multiaddr
        or an empty string). A hash this wallet already registered with the
        same name, compose and session is left as it is; a hash another
        wallet registered is refused, and so is a name another wallet owns.
        The record cannot be changed, so a hash registered with another
        compose or session is refused too: the nodes would fetch the compose
        the record names."""
        session = config.read("SECURELOCK_SESSION") or ""
        details = self.get_image_details(ipfs_hash)
        if details is not None and details.owner != "0x0000000000000000000000000000000000000000":
            if details.owner.lower() != self.acct.address.lower():
                raise Exception(f"{ipfs_hash} is registered by {details.owner}, not by this wallet")
            differing = [(field, recorded, wanted) for field, recorded, wanted in (
                ("name", details.name, self.enclave_name_securelock),
                ("compose", details.docker_compose_hash, docker_compose_hash),
                ("session", details.session, session),
            ) if recorded != wanted]
            if differing:
                recorded = ", ".join(f"{field} {value}" for field, value, _ in differing)
                wanted = ", ".join(f"{field} {value}" for field, _, value in differing)
                raise Exception(f"{ipfs_hash} is registered with {recorded}, and this publish has {wanted}; "
                                f"run ecld-build to publish a new version")
            print(f"\t✔  {ipfs_hash} is already registered")
            return False
        name_owner = self.image_name_owner(self.enclave_name_securelock)
        if name_owner is not None and int(name_owner, 16) != 0 and name_owner.lower() != self.acct.address.lower():
            raise Exception(f"the image name {self.enclave_name_securelock} belongs to {name_owner}; "
                            f"publish under another PROJECT_NAME")
        fee = int(config.read("DEVELOPER_FEE") or 0)
        gas_limit = 9000000 if self.blockchain_config.network == "bloxberg" else 1200000
        self._send(
            self.image_registry_contract.functions.registerImage(
                ipfs_hash, self.securelock_version, self.enclave_name_securelock,
                docker_compose_hash, session, fee, ipfs_peer or ""),
            gas_limit,
            f"Registering {self.enclave_name_securelock} {self.securelock_version} as {ipfs_hash}")
        reward_address = config.read("REWARD_ADDRESS")
        if reward_address:
            self.set_reward_address(ipfs_hash, reward_address)
        return True

    def set_image_cert(self, ipfs_hash, cert, wait_secs=600):
        """Write the certificate of a registered image, once, from the wallet
        that registered it. A certificate already there is left as it is.

        The RPC endpoint balances requests over nodes that can lag the chain
        by several blocks, and a read can fail outright, so an image this
        publish registered minutes earlier can read as missing, or be refused
        as "Image not found" by the simulation: both are tried again every 15
        seconds for `wait_secs` before the image is called missing."""
        gas_limit = 9000000 if self.blockchain_config.network == "bloxberg" else 1200000
        deadline = time.time() + wait_secs
        while True:
            details = self.get_image_details(ipfs_hash)
            if details is not None and details.owner != "0x0000000000000000000000000000000000000000":
                if details.public_key:
                    if details.public_key.strip() == cert.strip():
                        print(f"\t✔  the certificate of {ipfs_hash} is already registered")
                        return False
                    raise Exception(f"{ipfs_hash} already has a certificate, and it differs from the extracted one")
                try:
                    self._send(
                        self.image_registry_contract.functions.setImageCert(ipfs_hash, cert),
                        gas_limit,
                        f"Registering the certificate of {ipfs_hash}")
                    return True
                except Exception as e:
                    if "Image not found" not in str(e) or time.time() >= deadline:
                        raise
            elif time.time() >= deadline:
                raise Exception(f"{ipfs_hash} is not registered")
            time.sleep(15)

    def set_reward_address(self, ipfs_hash, reward_address):
        """Name where the image's developer fee is paid. The registry records
        the publishing wallet at registration; only the image's owner, the
        publisher, changes it. ECImageRegistryV3 refuses the zero address, so
        it is refused here before any transaction."""
        reward_address = self.provider.to_checksum_address(reward_address)
        if int(reward_address, 16) == 0:
            raise Exception("REWARD_ADDRESS is the zero address; leave it empty to be paid at the publishing wallet")
        current = self.image_registry_contract.functions.getRewardAddress(ipfs_hash).call()
        if current == reward_address:
            print(f"\t✔  reward address is already {reward_address}")
            return
        self._send(
            self.image_registry_contract.functions.changeImageRewardAddress(ipfs_hash, reward_address),
            200000,
            f"Setting the reward address to {reward_address}")
