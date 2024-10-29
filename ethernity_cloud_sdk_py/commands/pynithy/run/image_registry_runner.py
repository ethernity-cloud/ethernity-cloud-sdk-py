#!/usr/local/bin/python

import os

from image_registry import ImageRegistry
import re

try:
    print("*********************")

    def extract_certificate(certificate_string):
        pattern = r"(?<=-----BEGIN CERTIFICATE-----\n)(.*?)(?=\n-----END CERTIFICATE-----)"
        matches = re.findall(pattern, certificate_string, re.DOTALL)
        if matches:
            return matches[0].strip()
        else:
            return None


    def read_file_content(file_path):
        with open(file_path, 'r') as file:
            content = file.read()
        return content


    secure_lock = read_file_content('certificate.securelock.crt')
    # secure_lock = extract_certificate(secure_lock)
    print("SECURELOCK:")
    print(secure_lock)

    trusted_zone = read_file_content('certificate.trustedzone.crt')
    # trusted_zone = extract_certificate(trusted_zone)
    print("TRUSTEDZONE:")
    print(trusted_zone)
    ipfs_hash = read_file_content('IPFS_HASH.ipfs')
    ipfs_hash = ipfs_hash.strip()
    ipfs_docker_compose_hash = read_file_content('IPFS_DOCKER_COMPOSE_HASH.ipfs')
    ipfs_docker_compose_hash = ipfs_docker_compose_hash.strip()
    print("IPFS_HASH: " + ipfs_hash)
    imageRegistry = ImageRegistry()
    imageRegistry.add_trusted_zone_cert(trusted_zone, ipfs_hash, 'etny-pynithy-testnet', ipfs_docker_compose_hash, os.getenv("ENCLAVE_NAME_TRUSTEDZONE"), 10)
    imageRegistry.add_secure_lock_and_validate_image_cert(secure_lock, ipfs_hash, 'etny-pynithy-testnet', ipfs_docker_compose_hash, os.getenv("ENCLAVE_NAME_SECURELOCK"), 0)
    print("************************")
except Exception as e:
    print("An exception occurred: " + str(e))
