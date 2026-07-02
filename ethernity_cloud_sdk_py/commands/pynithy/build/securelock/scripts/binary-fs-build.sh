#!/bin/bash
# Runs in the SCONE crosscompiler stage (SCONE build tools, NO SGX needed).
# The PyInstaller freeze already happened OFF-SCONE in the pyfreeze stage; this
# stage has received (copied in):
#   /usr/local/bin/python3            - the static SCONE-built python 3.14
#   /usr/local/lib/python3.14/*       - its stdlib + the installed site-packages
#   /etny-securelock/securelock.py    - the rendered app (run at enclave runtime)
#   /etny-securelock/COLLECT-00.toc   - PyInstaller's collected .so list
#   /etny-securelock/get_sgx_report.so
#
# We bake the stdlib + site-packages + app + every collected .so into the
# measured binary-fs; the SCONE python then loads them via SCONE_EXTENSIONS_PATH
# at runtime (no dlopen from the untrusted host).
set -e
cd /etny-securelock

EXEC=(scone binary-fs / /binary-fs-dir -v \
  --include '/usr/local/bin/python3' \
  --include '/usr/local/lib/python3.14/*' \
  --include '/etny-securelock/*' \
  --host-path=/etc/resolv.conf \
  --host-path=/etc/hosts)

# Add every shared object PyInstaller collected (crypto/web3/etc. native libs).
if [ -f ./COLLECT-00.toc ]; then
  for FILE in $(grep '.so' ./COLLECT-00.toc | grep BINARY | awk -F "'" '{print $4}'); do
    EXEC+=(--include "${FILE}"'*')
  done
fi

echo "${EXEC[@]}"
mkdir -p /binary-fs-dir
SCONE_MODE=auto exec "${EXEC[@]}"
