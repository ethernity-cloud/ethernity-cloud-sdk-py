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


class etny_crypto:
    curve = registry.get_curve('secp384r1')

    @staticmethod
    def compress_point(point):
        return hex(point.x) + hex(point.y % 2)[2:]

    @staticmethod
    def ecc_point_to_256_bit_key(point):
        value = str(point.x) + str(point.y)
        sha = hashlib.sha256(value.encode())
        return sha.digest()

    @staticmethod
    def encrypt_aes_gcm(msg, secret_key):
        aes_cipher = AES.new(secret_key, AES.MODE_GCM)
        cipher_text, auth_tag = aes_cipher.encrypt_and_digest(msg)
        return (cipher_text, aes_cipher.nonce, auth_tag)

    @staticmethod
    def encrypt_ecc(msg, public_key):
        cipher_text_private_key = secrets.randbelow(etny_crypto.curve.field.n)
        shared_ecc_key = cipher_text_private_key * public_key
        secret_key = etny_crypto.ecc_point_to_256_bit_key(shared_ecc_key)
        ciphertext, nonce, auth_tag = etny_crypto.encrypt_aes_gcm(msg, secret_key)
        cipher_text_public_key = cipher_text_private_key * etny_crypto.curve.g
        return (ciphertext, nonce, auth_tag, cipher_text_public_key)

    @staticmethod
    def decrypt_ase_gcm(ciphertext, nonce, auth_tag, secret_key):
        aes_cipher = AES.new(secret_key, AES.MODE_GCM, nonce)
        plaintext = aes_cipher.decrypt_and_verify(ciphertext, auth_tag)
        return plaintext

    @staticmethod
    def decrypt_ecc(encrypted_msg, priv_key):
        (ciphertext, nonce, authTag, ciphertextPubKey) = encrypted_msg
        shared_ecc_key = priv_key * ciphertextPubKey
        secret_key = etny_crypto.ecc_point_to_256_bit_key(shared_ecc_key)
        plaintext = etny_crypto.decrypt_ase_gcm(ciphertext, nonce, authTag, secret_key)
        return plaintext

    @staticmethod
    def ecc_read_key(public_key_file):
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
        ecc_public_cert = ECC.import_key(pub_key_content)
        pub_key_ecc_point = ec.Point(etny_crypto.curve, ecc_public_cert.pointQ.x.__int__(),
                                     ecc_public_cert.pointQ.y.__int__())
        encrypted_msg = etny_crypto.encrypt_ecc(message, pub_key_ecc_point)
        return encrypted_msg

    @staticmethod
    def clean_private_key(private_key_data):
        private_key_data = private_key_data.replace(b"-----BEGIN PRIVATE KEY-----", b"")
        private_key_data = private_key_data.replace(b"-----END PRIVATE KEY-----", b"")
        private_key_data = codecs.decode(private_key_data, 'base64')
        return private_key_data

    @staticmethod
    def decrypt(private_key_file, encrypted_msg):
        # Reading and calculating Private Key from PEM
        with open(private_key_file) as f:
            private_key_data = str.encode(f.read())

        private_key_data = etny_crypto.clean_private_key(private_key_data)
        # decode der with asn1 library
        # - get the octet string (field-2) containing the raw key
        asn1_object, _ = decoder.decode(private_key_data)
        raw_keys = asn1_object.getComponentByName('field-2').asOctets()
        # - get the octet string (field-1) containing the raw private key
        #   and the bit string (field-2) containing the uncompressed public key
        asn1_object, _ = decoder.decode(raw_keys)
        private_key = asn1_object.getComponentByName('field-1').asOctets()

        # Generating keypair for tinyEC
        priv_key_tec = int.from_bytes(private_key, byteorder="big")
        decrypted_msg = etny_crypto.decrypt_ecc(encrypted_msg, priv_key_tec)
        #print("\ndecrypted msg:", decrypted_msg)
        return decrypted_msg

    @staticmethod
    def encrypted_data_to_base64_json(encrypted_msg):
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
        # decode the base64-encoded string back to a JSON string
        decoded_json_data = base64.b64decode(base64_data).decode('utf-8')

        json_load = json.loads(decoded_json_data)
        return (binascii.unhexlify(json_load['ciphertext'].encode()),
                binascii.unhexlify(json_load['nonce'].encode()),
                binascii.unhexlify(json_load['authTag'].encode()),
                ec.Point(etny_crypto.curve, int(json_load['x'], 16),
                         int(json_load['y'], 16)))

    @staticmethod
    def hex_to_bytes(hex_str: str) -> bytes:
        return bytes.fromhex(hex_str[2:] if hex_str[:2] == "0x" else hex_str)

    @staticmethod
    def encrypt_nacl(public_key: str, data: bytes) -> str:
        """Encryption function using NaCl box compatible with MetaMask
        For implementation used in MetaMask look into: https://github.com/MetaMask/eth-sig-util
        Args:
            public_key: public key of recipient (32 bytes)
            data: message data
        Returns:
            encrypted data
        """
        emph_key = PrivateKey.generate()
        enc_box = Box(emph_key, PublicKey(etny_crypto.hex_to_bytes(public_key)))
        # Encryption must work with MetaMask decryption (requires valid utf-8)
        data = a85encode(data)
        ciphertext = enc_box.encrypt(data)
        result = bytes(emph_key.public_key) + ciphertext
        return result.hex()


if __name__ == '__main__':
    encrypted_msg_ = etny_crypto.encrypt('./app/cert1-ca1-clean.crt', b'test')
    #print(encrypted_msg_)
    # etny_crypto.decrypt('./certs/cert1-ca1-clean.key', encrypted_msg_)
    #print(etny_crypto.encrypted_data_to_base64_json(encrypted_msg_))
