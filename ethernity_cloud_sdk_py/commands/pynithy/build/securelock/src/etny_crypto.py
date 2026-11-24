"""The securelock's copy of the trustedzone's etny_crypto (etny-pynithy
v3/build/trustedzone/src/etny_crypto.py): every function this copy shares
with it by name is identical to it, but for the trustedzone's comment lines
that hold a print call, which this copy does not carry. The trustedzone's
statement signer (the form 0xFF || "ecld-stmt-1" || 0x00 || statement of
pox-smart-contract-litvm spec reserved-prefixes.json), which signs the
trustedzone's own statements, and get_wallet_address, the derivation of the
task wallet, are the trustedzone's alone and are not in this copy; encrypt,
of a public key file, is this copy's alone."""
from __future__ import annotations

import base64
import binascii
import codecs
import hashlib
import json
import secrets
from pyasn1.codec.der import decoder
from Crypto.Cipher import AES
from tinyec import registry
from tinyec import ec
from Crypto.PublicKey import ECC
from nacl.public import Box, PrivateKey, PublicKey
from base64 import a85encode
from eth_account import  Account
import ecdsa
from ecdsa.util import sigencode_der, sigdecode_der


class etny_crypto:
    curve = registry.get_curve('secp384r1')

    @staticmethod
    def compress_point(point):
        """
        Compress an ECC point to a hexadecimal string.

        Args:
            point (ec.Point): The ECC point to compress.

        Returns:
            str: Hex string of x-coordinate concatenated with parity of y (0 or 1).
        """
        return hex(point.x) + hex(point.y % 2)[2:]

    @staticmethod
    def ecc_point_to_256_bit_key(point):
        """
        Derive a 256-bit key from an ECC point by hashing concatenated x and y coordinates.

        Args:
            point (ec.Point): The ECC point.

        Returns:
            bytes: SHA-256 digest of the point's coordinates.
        """
        value = str(point.x) + str(point.y)
        sha = hashlib.sha256(value.encode())
        return sha.digest()

    @staticmethod
    def encrypt_aes_gcm(msg, secret_key):
        """
        Encrypt a message using AES-GCM with the provided secret key.

        Args:
            msg (bytes): The plaintext message.
            secret_key (bytes): The 256-bit symmetric key.

        Returns:
            tuple: (ciphertext, nonce, auth_tag)
        """
        aes_cipher = AES.new(secret_key, AES.MODE_GCM)
        cipher_text, auth_tag = aes_cipher.encrypt_and_digest(msg)
        return (cipher_text, aes_cipher.nonce, auth_tag)

    @staticmethod
    def encrypt_ecc(msg, public_key):
        """
        Perform hybrid ECC encryption: Generate ephemeral keypair, derive shared secret,
        hash to AES key, and encrypt with AES-GCM.

        Args:
            msg (bytes): The plaintext message.
            public_key (ec.Point): Recipient's public key point.

        Returns:
            tuple: (ciphertext, nonce, auth_tag, ephemeral_public_key_point)
        """
        cipher_text_private_key = secrets.randbelow(etny_crypto.curve.field.n)
        shared_ecc_key = cipher_text_private_key * public_key
        secret_key = etny_crypto.ecc_point_to_256_bit_key(shared_ecc_key)
        ciphertext, nonce, auth_tag = etny_crypto.encrypt_aes_gcm(msg, secret_key)
        cipher_text_public_key = cipher_text_private_key * etny_crypto.curve.g
        return (ciphertext, nonce, auth_tag, cipher_text_public_key)

    @staticmethod
    def decrypt_ase_gcm(ciphertext, nonce, auth_tag, secret_key):
        """
        Decrypt AES-GCM ciphertext.

        Args:
            ciphertext (bytes): Encrypted data.
            nonce (bytes): Nonce used in encryption.
            auth_tag (bytes): Authentication tag.
            secret_key (bytes): Symmetric key.

        Returns:
            bytes: Decrypted plaintext.

        Raises:
            ValueError: If verification fails.
        """
        aes_cipher = AES.new(secret_key, AES.MODE_GCM, nonce)
        plaintext = aes_cipher.decrypt_and_verify(ciphertext, auth_tag)
        return plaintext

    @staticmethod
    def decrypt_ecc(encrypted_msg, priv_key):
        """
        Decrypt hybrid ECC-encrypted message: Derive shared secret from private key and
        ephemeral public key, hash to AES key, and decrypt.

        Args:
            encrypted_msg (tuple): (ciphertext, nonce, auth_tag, ephemeral_pub_key_point)
            priv_key (int): Recipient's private key scalar.

        Returns:
            bytes: Decrypted plaintext.
        """
        (ciphertext, nonce, authTag, ciphertextPubKey) = encrypted_msg
        shared_ecc_key = priv_key * ciphertextPubKey
        secret_key = etny_crypto.ecc_point_to_256_bit_key(shared_ecc_key)
        plaintext = etny_crypto.decrypt_ase_gcm(ciphertext, nonce, authTag, secret_key)
        return plaintext

    @staticmethod
    def ecc_read_key(public_key_file):
        """
        Read and import an ECC public key from a PEM file.

        Args:
            public_key_file (str): Path to the PEM file.

        Returns:
            ECC.EccKey: Imported ECC key object.
        """
        with open(public_key_file) as r:
            key = ECC.import_key(r.read())
            return key

    @staticmethod
    def encrypt(public_key_file, message):
        with open(public_key_file) as f:
            ecc_public_cert = ECC.import_key(f.read())
            pub_key_ecc_point = ec.Point(etny_crypto.curve, ecc_public_cert.pointQ.x.__int__(),
                                         ecc_public_cert.pointQ.y.__int__())
            encrypted_msg = etny_crypto.encrypt_ecc(message, pub_key_ecc_point)
            return encrypted_msg

    @staticmethod
    def encrypt_with_pub_key(pub_key_content, message):
        """
        Encrypt a message using a provided ECC public key in PEM format.

        Args:
            pub_key_content (bytes): PEM-encoded public key.
            message (bytes): Plaintext message.

        Returns:
            tuple: Encrypted tuple from encrypt_ecc.
        """
        ecc_public_cert = ECC.import_key(pub_key_content)
        pub_key_ecc_point = ec.Point(etny_crypto.curve, ecc_public_cert.pointQ.x.__int__(),
                                     ecc_public_cert.pointQ.y.__int__())
        encrypted_msg = etny_crypto.encrypt_ecc(message, pub_key_ecc_point)
        return encrypted_msg

    @staticmethod
    def clean_private_key(private_key_data):
        """
        Clean a PEM private key by removing headers and decoding Base64.

        Args:
            private_key_data (bytes): Raw PEM data.

        Returns:
            bytes: Base64-decoded private key.
        """
        private_key_data = private_key_data.replace(b"-----BEGIN PRIVATE KEY-----", b"")
        private_key_data = private_key_data.replace(b"-----END PRIVATE KEY-----", b"")
        private_key_data = codecs.decode(private_key_data, 'base64')
        return private_key_data

    @staticmethod
    def decrypt(private_key, encrypted_msg):
        """
        Decrypt an encrypted message using a PEM private key.

        This method cleans the PEM, decodes ASN.1 to extract the raw private key scalar,
        and calls decrypt_ecc.

        Args:
            private_key (bytes): PEM-encoded private key.
            encrypted_msg (tuple): Encrypted tuple.

        Returns:
            bytes: Decrypted plaintext.
        """
        # Reading and calculating Private Key from PEM
        private_key = etny_crypto.clean_private_key(private_key)
        # decode der with asn1 library
        # - get the octet string (field-2) containing the raw key
        asn1_object, _ = decoder.decode(private_key)
        raw_keys = asn1_object.getComponentByName('field-2').asOctets()
        # - get the octet string (field-1) containing the raw private key
        #   and the bit string (field-2) containing the uncompressed public key
        asn1_object, _ = decoder.decode(raw_keys)
        private_key = asn1_object.getComponentByName('field-1').asOctets()
        # Generating keypair for tinyEC
        priv_key_tec = int.from_bytes(private_key, byteorder="big")
        decrypted_msg = etny_crypto.decrypt_ecc(encrypted_msg, priv_key_tec)
        return decrypted_msg

    @staticmethod
    def encrypted_data_to_base64_json(encrypted_msg):
        """
        Serialize an encrypted tuple to Base64-encoded JSON.

        Args:
            encrypted_msg (tuple): (ciphertext, nonce, auth_tag, point)

        Returns:
            bytes: Base64-encoded JSON string.
        """
        json_obj = {
            'ciphertext': binascii.hexlify(encrypted_msg[0]).decode(),
            'nonce': binascii.hexlify(encrypted_msg[1]).decode(),
            'authTag': binascii.hexlify(encrypted_msg[2]).decode(),
            'x': hex(encrypted_msg[3].x),
            'y': hex(encrypted_msg[3].y)
        }
        json_string = json.dumps(json_obj)
        binary_data = json_string.encode('utf-8')
        base64_data = base64.b64encode(binary_data)
        return base64_data

    @staticmethod
    def encrypted_data_from_base64_json(base64_data):
        """
        Deserialize Base64-encoded JSON back to an encrypted tuple.

        Args:
            base64_data (bytes): Base64-encoded JSON.

        Returns:
            tuple: (ciphertext, nonce, auth_tag, ec.Point)
        """
        # decode the base64-encoded string back to a JSON string
        decoded_json_data = base64.b64decode(base64_data).decode('utf-8')
        json_load = json.loads(decoded_json_data)
        # The coordinates come from an untrusted ciphertext tuple. tinyec's
        # Point only warns when (x, y) is not on the curve and multiplies it
        # anyway, and its scalar multiplication never uses the curve's b, so
        # an off-curve point multiplied by the identity scalar would leak that
        # scalar modulo the point's order through the AES-GCM tag oracle.
        # Reject any point not on secp384r1; the curve has cofactor 1, so every
        # on-curve point other than infinity has the full group order.
        point = ec.Point(etny_crypto.curve, int(json_load['x'], 16),
                         int(json_load['y'], 16))
        if not point.on_curve:
            raise ValueError("ECIES public point rejected: not on secp384r1")
        return (binascii.unhexlify(json_load['ciphertext'].encode()),
                binascii.unhexlify(json_load['nonce'].encode()),
                binascii.unhexlify(json_load['authTag'].encode()),
                point)

    @staticmethod
    def hex_to_bytes(hex_str: str) -> bytes:
        """
        Convert a hex string (with or without '0x' prefix) to bytes.

        Args:
            hex_str (str): Hexadecimal string.

        Returns:
            bytes: Corresponding byte array.
        """
        return bytes.fromhex(hex_str[2:] if hex_str[:2] == "0x" else hex_str)

    @staticmethod
    def encrypt_nacl(public_key: str, data: bytes) -> str:
        """
        Encryption function using NaCl box compatible with MetaMask.
        For implementation used in MetaMask look into: https://github.com/MetaMask/eth-sig-util

        Args:
            public_key (str): Hex-encoded public key of recipient (32 bytes, without '0x').
            data (bytes): Message data.

        Returns:
            str: Hex-encoded result (ephemeral pubkey + ciphertext).
        """
        emph_key = PrivateKey.generate()
        enc_box = Box(emph_key, PublicKey(etny_crypto.hex_to_bytes(public_key)))
        # Encryption must work with MetaMask decryption (requires valid utf-8)
        data = a85encode(data)
        ciphertext = enc_box.encrypt(data)
        result = bytes(emph_key.public_key) + ciphertext
        return result.hex()

    @staticmethod
    def get_private_key_int(private_key_pem: bytes) -> int:
        """
        Extracts the raw private key integer from a PEM-encoded private key via ASN.1 decoding.
        Args:
            private_key_pem (bytes): PEM-encoded private key.
        Returns:
            int: The private key scalar.
        """
        private_key = etny_crypto.clean_private_key(private_key_pem)
        asn1_object, _ = decoder.decode(private_key)
        raw_keys = asn1_object.getComponentByName('field-2').asOctets()
        asn1_object, _ = decoder.decode(raw_keys)
        private_key_bytes = asn1_object.getComponentByName('field-1').asOctets()
        return int.from_bytes(private_key_bytes, byteorder="big")

    @staticmethod
    def sign_data(private_key_pem: bytes, data: bytes) -> bytes:
        """
        Signs data using the private key with ECDSA (secp384r1, SHA-256) and returns DER-encoded signature.
        Args:
            private_key_pem (bytes): PEM-encoded private key.
            data (bytes): Data to sign.
        Returns:
            bytes: DER-encoded signature.
        """
        priv_int = etny_crypto.get_private_key_int(private_key_pem)
        priv_bytes_len = (priv_int.bit_length() + 7) // 8
        priv_bytes = priv_int.to_bytes(priv_bytes_len, 'big')
        sk = ecdsa.SigningKey.from_secret_exponent(priv_int, curve=ecdsa.curves.NIST384p, hashfunc=hashlib.sha256)
        sig_der = sk.sign_deterministic(data, hashfunc=hashlib.sha256, sigencode=sigencode_der)
        vk = sk.get_verifying_key()
        pub_key_bytes = vk.to_string('raw')
        return sig_der

    @staticmethod
    def verify_signature(public_key_hex: str, data: bytes, sig_der: bytes) -> bool:
        if public_key_hex.startswith('0x'):
            public_key_hex = public_key_hex[2:]
        pub_bytes = bytes.fromhex(public_key_hex)
        curve = ecdsa.curves.NIST384p
        try:
            vk = ecdsa.VerifyingKey.from_string(
                pub_bytes,
                curve=curve,
                hashfunc=hashlib.sha256,
                valid_encodings={"raw", "compressed", "uncompressed", "hybrid"}  # Add 'raw' here
            )
            assert vk.verify(sig_der, data, hashfunc=hashlib.sha256, sigdecode=sigdecode_der)
            return True
        except:
            return False
