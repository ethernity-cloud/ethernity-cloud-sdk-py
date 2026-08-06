#!/usr/bin/env python3
"""Decrypt the SGX key-generation source for the compile step, then it is shredded.

The source (get_sgx_report.c) ships ENCRYPTED in the SDK package so it is not
readable as plaintext by anyone inspecting the wheel or the build tree. This
runs inside the SCONE cross-compiler stage, writes the plaintext to a tmpfs
path for the duration of the compile only, and the caller shreds it right
after.

Usage:  decrypt_keygen.py <in.enc> <out.c> [hex_key]

The wrapping key defaults to a value derived from a fixed label; a hex key may
be supplied (build arg) to rotate it. This hides the SOURCE, not the algorithm:
the compiled .so still contains the logic and the enclave image is public. Key
secrecy on-chain comes from attestation, not from hiding this file.
"""

import hashlib
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_LABEL = b"ethernity-cloud/sgx-keygen-wrap/v1"


def _key(hex_key):
    if hex_key:
        return bytes.fromhex(hex_key)
    return hashlib.sha256(_LABEL).digest()


def _nonce():
    return hashlib.sha256(_LABEL + b"nonce").digest()[:12]


def main():
    in_path = sys.argv[1]
    out_path = sys.argv[2]
    hex_key = sys.argv[3] if len(sys.argv) > 3 else ""

    ciphertext = open(in_path, "rb").read()
    plaintext = AESGCM(_key(hex_key)).decrypt(_nonce(), ciphertext, b"get_sgx_report.c")
    with open(out_path, "wb") as f:
        f.write(plaintext)


if __name__ == "__main__":
    main()
