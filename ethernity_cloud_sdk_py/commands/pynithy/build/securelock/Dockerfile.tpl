# syntax=docker/dockerfile:1
#
# Securelock enclave build. The whole build is SGX-free: the Python packages
# are installed on a STOCK python:3.14-alpine with no SCONE and no SGX; only
# the binary-fs packaging and signing use the SCONE build tools, which run in
# simulation/build mode without an SGX device.
#
# The binary-fs is made of /enclave-root, a directory that holds the enclave's
# files and nothing else (scripts/enclave-root.sh); the launcher refuses a
# start whose file system holds another entry (fixed-start/fixed_start.c).

########################################################################
# Stage 1 - SGX report helper (unchanged, crosscompiler build tool)
########################################################################
FROM registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/sconecuratedimages/crosscompilers:alpine-scone6.0.7 AS build-sgx-module
# The SDK ships the SGX key-gen module ONLY as a PREBUILT .so
# (get_sgx_report.so) -- never the .c source. The source of truth for
# get_sgx_report.c lives in the etny-pynithy / etny-nodenithy repos; the .so is
# produced once from it via scripts/build_keygen_so.sh (scone-gcc, hardened +
# stripped) and committed here. No source is compiled during the developer's
# build, so a developer inspecting the wheel or build tree never sees the
# derivation algorithm.
#
# NOTE (see esr_wallet.py): the .so still contains the executable logic and the
# enclave image is public, so this hides the SOURCE, not the algorithm; on
# testnet the key remains reproducible because there is no attestation.
COPY src/get_sgx_report.so /etny-securelock/get_sgx_report.so

########################################################################
# Stage 2 - the enclave's root, on a NORMAL python 3.14 (no SCONE / no SGX)
# Installs the securelock's packages and the user's requirements.txt, renders
# securelock.py and assembles the enclave's files in /enclave-root. Same
# python version + musl as the SCONE runtime python, so the installed
# packages are compatible.
########################################################################
FROM python:3.14-alpine AS pyfreeze
RUN apk add --no-cache bash gcc g++ musl-dev libffi-dev openssl-dev rust cargo make libsodium-dev zlib-dev binutils
ENV SODIUM_INSTALL=system
WORKDIR /etny-securelock

# Core securelock runtime deps (built here, off-SCONE, where pip and native
# builds work normally).
RUN pip install --no-cache-dir \
      "web3>=6" cryptography ecdsa pyasn1 tinyec minio pynacl

# User-supplied additional modules (the customization hook).
COPY ./src/serverless/requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt || true

COPY ./src /etny-securelock/
COPY ./scripts/* /etny-securelock/
COPY --from=build-sgx-module /etny-securelock/get_sgx_report.so /etny-securelock/get_sgx_report.so

# Render securelock.py from the template and compile site-packages' bytecode.
ENV SECURELOCK_SESSION=__SECURELOCK_SESSION__
ENV BUCKET_NAME=__BUCKET_NAME__
ENV SMART_CONTRACT_ADDRESS=__SMART_CONTRACT_ADDRESS__
ENV IMAGE_REGISTRY_ADDRESS=__IMAGE_REGISTRY_ADDRESS__
ENV RPC_URL=__RPC_URL__
ENV CHAIN_ID=__CHAIN_ID__
ENV TRUSTED_ZONE_IMAGE=__TRUSTED_ZONE_IMAGE__
ENV NETWORK_TYPE=__NETWORK_TYPE__
ENV VALIDATOR_REGISTRY_ADDRESS=__VALIDATOR_REGISTRY_ADDRESS__
RUN chmod +x /etny-securelock/pyfreeze.sh && /etny-securelock/pyfreeze.sh

# SCONE-built static python + its stdlib, from the runtime base image, beside
# this stage's own interpreter.
COPY --from=etny-securelock-serverless /usr/local/bin/python3 /scone-python/bin/python3
COPY --from=etny-securelock-serverless /usr/local/lib/python3.14 /scone-python/lib/python3.14
# The enclave's root, /enclave-root: the SCONE python and its stdlib, this
# stage's site-packages and /etny-securelock (the rendered securelock.py, its
# sibling modules etny_crypto.py, etny_exec.py, models.py,
# swift_stream_service.py, the abi/ dir, get_sgx_report.so, the dApp's
# serverless package, the scripts), and the shared libraries their ELF files
# need, which fixed-start/elf_needed.sh takes from this stage and lists in
# /fixed_start_elf.txt (scripts/enclave-root.sh).
COPY ./fixed-start/ /fixed-start/
RUN bash /etny-securelock/enclave-root.sh

########################################################################
# Stage 3 - binary-fs packaging with the SCONE build tools (no SGX).
# Makes the measured binary-fs of the enclave's root, whole, writes the root's
# digest, then cross-compiles the binary-fs to libbinary-fs.so.
########################################################################
FROM registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/sconecuratedimages/crosscompilers:alpine-scone6.0.7 AS binfs
COPY --from=pyfreeze /enclave-root /enclave-root
COPY --from=pyfreeze /fixed_start_elf.txt /binary-fs-dir/fixed_start_elf.txt
RUN bash /enclave-root/etny-securelock/binary-fs-build.sh
# The fixed start (fixed-start/fixed_start.c, with the securelock's argument
# vector, environment, working directory and excepted entries in
# fixed-start/securelock.h and the name of its CAS session in
# FIXED_START_SESSION) is linked into the extension, which the signer
# measures: the enclave runs the ENTRYPOINT's vector with the header's
# variables and the listed keys of its environment, whatever arguments and
# environment its start gives it, and only when its file system holds the
# entries of /enclave-root and, beside them, the excepted entries alone. The
# digest of the root is written here, from the directory the binary-fs above
# is made of (fixed-start/tree_digest.sh, the last step that reads the root),
# with the listing of the entries it covers, and compiled into the launcher.
COPY ./fixed-start/ /fixed-start/
RUN bash /fixed-start/tree_digest.sh /enclave-root /binary-fs-dir/fixed_start_tree.h /binary-fs-dir/fixed_start_tree.txt
RUN cd /binary-fs-dir && scone gcc ./binary_fs_blob.s -O2 -fPIC -DFIXED_START_SESSION='"__SECURELOCK_SESSION__"' -include /fixed-start/securelock.h -include /binary-fs-dir/fixed_start_tree.h /fixed-start/fixed_start.c ./libbinary_fs_template.a -shared -o /libbinary-fs.so

########################################################################
# Stage 4 - final SCONE runtime image (lean scone python base) + sign.
########################################################################
FROM etny-securelock-serverless

COPY --from=binfs /usr/local/bin/scone /usr/local/bin/scone
COPY --from=binfs /libbinary-fs.so /lib/libbinary-fs.so

RUN openssl genrsa -3 -out /enclave-key.pem 3072

# SCONE enclaves run with no HOME; libraries calling os.path.expanduser('~')
# abort with "$HOME is not defined" and kill the enclave before it emits its
# public key. Set a writable HOME so startup (and cert emission) succeeds.
ENV HOME=/tmp
ENV SCONE_HEAP=__MEMORY_TO_ALLOCATE__
ENV SCONE_LOG=FATAL
ENV SCONE_DEBUG=0
ENV SCONE_STACK=4M
__SCONE_ALLOW_DLOPEN__
ENV SCONE_EXTENSIONS_PATH=/lib/libbinary-fs.so
# securelock.py does relative imports of its sibling modules (etny_crypto,
# models, ...) and reads abi/ by relative path; run from its own directory so
# those resolve (the base image's build WORKDIR /b/Python-3.14.6 otherwise leaks
# in as the enclave PWD).
ENV SCONE_PWD=/etny-securelock
__ESR_ENV__
WORKDIR /etny-securelock

# Rendered by build.py: signs the EXECUTED binary (/usr/local/bin/python) with
# explicit enclave-creation params; mainnet adds --production, testnet omits it
# (non-CAS self-sign mode).
__SCONE_SIGN__

# Record the SIGNED MRENCLAVE so publish.py can assert the runtime enclave
# measurement matches what was signed BEFORE registering the CAS session /
# publishing (mismatch => SCONE recomputed at load => debug => CAS rejects).
# scone-signer info prints two tab-indented "MRENCLAVE:" lines (non-EDMM first;
# runtime has EDMM disabled, so the first is what SCONE_HASH produces). Capture
# to a file first: piping into `head` kills scone-signer with SIGPIPE (exit 141)
# under buildkit's `ash -eo pipefail`.
RUN scone-signer info /usr/local/bin/python > /tmp/siginfo.txt 2>&1; \
    grep -iE "MRENCLAVE:" /tmp/siginfo.txt | sed -n '1p' \
      | sed -E 's/.*MRENCLAVE:[[:space:]]*//I' | tr -d '[:space:]' > /signed_mrenclave.txt && \
    echo "SIGNED_MRENCLAVE=$(cat /signed_mrenclave.txt)"

RUN rm -rf /enclave-key.pem

ENTRYPOINT ["/usr/local/bin/python", "-I", "/etny-securelock/securelock.py"]
