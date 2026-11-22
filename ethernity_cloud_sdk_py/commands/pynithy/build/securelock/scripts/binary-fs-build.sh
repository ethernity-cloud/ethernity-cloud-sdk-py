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

# The shared objects PyInstaller collected, read from its toc before its
# output is removed. PyInstaller's output (build/: its analysis, the PYZ
# archive and the frozen executable; dist/: the frozen application with a copy
# of every collected library) is not run by the enclave, which runs
# securelock.py with the SCONE interpreter, and differs from build to build:
# it stays out of the binary-fs, as in the trustedzone's binary-fs-build.sh.
COLLECTED_SO=()
if [ -f ./COLLECT-00.toc ]; then
  while IFS= read -r FILE; do COLLECTED_SO+=("${FILE}"); done < <(grep '.so' ./COLLECT-00.toc | grep BINARY | awk -F "'" '{print $4}')
fi
rm -rf ./build ./dist ./*.spec ./COLLECT-00.toc

EXEC=(scone binary-fs / /binary-fs-dir -v \
  --include '/usr/local/bin/python3' \
  --include '/usr/local/lib/python3.14/*' \
  --include '/etny-securelock/*' \
  --host-path=/etc/resolv.conf \
  --host-path=/etc/hosts)

# Add every shared object PyInstaller collected (crypto/web3/etc. native libs).
for FILE in "${COLLECTED_SO[@]}"; do
  EXEC+=(--include "${FILE}"'*')
done

echo "${EXEC[@]}"
mkdir -p /binary-fs-dir
SCONE_MODE=auto exec "${EXEC[@]}"
