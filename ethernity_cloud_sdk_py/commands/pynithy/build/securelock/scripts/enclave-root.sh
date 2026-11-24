#!/bin/bash
# Runs in the pyfreeze stage, after pyfreeze.sh. Assembles the enclave's
# root, /enclave-root: a new directory that holds the enclave's files and
# nothing else, each at its path in the enclave. The binfs stage makes the
# binary-fs of it whole (binary-fs-build.sh), fixed-start/tree_digest.sh
# writes its digest, and the launcher refuses a start whose file system holds
# another entry (fixed-start/fixed_start.c). The root holds:
#   /usr/local/bin/python3      the SCONE-built python 3.14, which holds its
#                               extension modules and names the system
#                               libraries they use as its needed libraries,
#                               and
#   /usr/local/lib/python3.14   its stdlib, both from the runtime base image
#                               (the Dockerfile copies them to /scone-python),
#                               without the interpreter's static archive
#                               libpython3.14.a, which nothing loads, and
#                               without the base's site-packages (pip,
#                               setuptools, wheel and packaging);
#   /usr/local/lib/python3.14/site-packages
#                               this stage's, as pip installed the
#                               securelock's packages and the dApp's
#                               requirements, without pip itself;
#   /etny-securelock            this directory as it is: the securelock and
#                               the dApp's serverless package;
#   /etny-securelock/fixed-start/openssl.cnf
#                               an empty file, the OpenSSL configuration the
#                               fixed environment names
#                               (fixed-start/securelock.h); its directory,
#                               which holds nothing else, is that
#                               environment's module and engine directory;
#   the shared libraries the root's ELF files need and the root does not
#   hold, which fixed-start/elf_needed.sh copies from this stage's /lib,
#   /usr/local/lib and /usr/lib: the interpreter's needed libraries, and a
#   package's extension module's when its wheel does not carry them.
# mkdir fails when the root exists. The binary-fs holds no empty directory,
# so the root holds none. The ELF files of the root, with the libraries they
# need and their run paths, are listed in /fixed_start_elf.txt.
set -euo pipefail

ROOT=/enclave-root
STDLIB=/usr/local/lib/python3.14

mkdir "${ROOT}"
mkdir -p "${ROOT}/usr/local/bin" "${ROOT}/usr/local/lib"
cp /scone-python/bin/python3 "${ROOT}/usr/local/bin/python3"
cp -a /scone-python/lib/python3.14 "${ROOT}${STDLIB}"
rm -rf "${ROOT}${STDLIB}/site-packages"
find "${ROOT}${STDLIB}" -name libpython3.14.a -exec rm {} \;
cp -a "${STDLIB}/site-packages" "${ROOT}${STDLIB}/site-packages"
rm -rf "${ROOT}${STDLIB}/site-packages/pip" "${ROOT}${STDLIB}/site-packages"/pip-*.dist-info
cp -a /etny-securelock "${ROOT}/etny-securelock"
mkdir "${ROOT}/etny-securelock/fixed-start"
: > "${ROOT}/etny-securelock/fixed-start/openssl.cnf"
find "${ROOT}" -depth -type d -empty -exec rmdir {} \;

bash /fixed-start/elf_needed.sh "${ROOT}" /fixed_start_elf.txt /
