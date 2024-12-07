from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import padding
from web3 import Web3
from eth_account import Account
import os
import base64

from pathlib import Path
from ethernity_cloud_sdk_py.commands.config import Config, config

config = Config(Path(".config.json").resolve())
config.load()


# Constants for key derivation
SALT_LENGTH = 16  # Length of the salt
ITERATIONS = 100000  # PBKDF2 iterations
KEY_LENGTH = 32  # AES 256-bit key

class PrivateKeyManager:
    def __init__(self, password: str):
        """Initialize with a password for key derivation."""
        self.password = password

    def derive_key(self, salt: bytes) -> bytes:
        """Derive a key from the password using PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_LENGTH,
            salt=salt,
            iterations=ITERATIONS,
            backend=default_backend()
        )
        return kdf.derive(self.password.encode())

    def encrypt_private_key(self, private_key: str) -> str:
        """Encrypt the private key using the password."""
        salt = os.urandom(SALT_LENGTH)  # Generate a random salt
        key = self.derive_key(salt)

        # Create AES cipher
        iv = os.urandom(16)  # AES block size is 16 bytes
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()

        # Pad the private key to be a multiple of the block size
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        padded_data = padder.update(private_key.encode()) + padder.finalize()

        # Encrypt the private key
        encrypted_private_key = encryptor.update(padded_data) + encryptor.finalize()

        # Return the encrypted key and salt (base64-encoded for storage)
        return base64.b64encode(salt + iv + encrypted_private_key).decode()

    def decrypt_private_key(self, encrypted_private_key: str) -> str:
        """Decrypt the private key using the password."""
        # Decode the base64-encoded data
        data = base64.b64decode(encrypted_private_key)

        # Extract the salt, IV, and the encrypted private key from the data
        salt = data[:SALT_LENGTH]
        iv = data[SALT_LENGTH:SALT_LENGTH + 16]
        encrypted_data = data[SALT_LENGTH + 16:]

        # Derive the key from the password and salt
        key = self.derive_key(salt)

        # Create AES cipher
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()

        # Decrypt the private key
        decrypted_data = decryptor.update(encrypted_data) + decryptor.finalize()

        # Unpad the decrypted data
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        private_key = unpadder.update(decrypted_data) + unpadder.finalize()

        return private_key.decode()
    
    def extract_address_from_private_key(self, private_key: str) -> str:
        """
        Extract the Ethereum address from a given private key.

        Args:
            private_key (str): The Ethereum private key in hexadecimal format (without the "0x" prefix).

        Returns:
            str: The Ethereum address derived from the private key.
        """
        #w3 = Web3(Web3.HTTPProvider(config.read("NETWORK_RPC")))


        # Ensure the private key is in the correct format
        if not private_key.startswith('0x'):
            private_key = '0x' + private_key

        # Use the private key to get the account and address
        account = Account().from_key(private_key)

        return account.address

# Example usage
#password = "mySecurePassword123"
#private_key = "77d6DDD127fe3a2bb79df2a02f0f408339"

# Create an instance of PrivateKeyManager
#key_manager = PrivateKeyManager(password)

# Encrypt the private key
#encrypted_key = key_manager.encrypt_private_key(private_key)
#print(f"Encrypted Private Key: {encrypted_key}")

# Decrypt the private key (this will prompt for the password)
#decrypted_key = key_manager.decrypt_private_key(encrypted_key)
#print(f"Decrypted Private Key: {decrypted_key}")
