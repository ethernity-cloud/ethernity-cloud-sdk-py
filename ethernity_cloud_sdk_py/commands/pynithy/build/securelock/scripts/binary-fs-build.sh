#!/bin/bash
# Runs in the SCONE crosscompiler stage (SCONE build tools, NO SGX needed), on
# /enclave-root: the enclave's root the pyfreeze stage assembled
# (enclave-root.sh), copied here whole by the Dockerfile. It holds the static
# SCONE-built python 3.14 and its stdlib, the installed site-packages, the
# rendered securelock.py with its sibling modules, the dApp's serverless
# package and get_sgx_report.so, and the shared libraries their ELF files
# need.
#
# The measured binary-fs is made of that root: the root whole (no --include),
# its symbolic links as they are (--preserve-symbolic-links: a link's target
# is a path of the enclave), and the host's two name-resolution files passed
# through. The SCONE python then reads its files from the binary-fs at
# runtime (SCONE_EXTENSIONS_PATH), never from the untrusted host.
set -e

EXEC=(scone binary-fs /enclave-root /binary-fs-dir -v \
  --preserve-symbolic-links \
  --host-path=/etc/resolv.conf \
  --host-path=/etc/hosts)

echo "${EXEC[@]}"
mkdir -p /binary-fs-dir
SCONE_MODE=auto exec "${EXEC[@]}"
