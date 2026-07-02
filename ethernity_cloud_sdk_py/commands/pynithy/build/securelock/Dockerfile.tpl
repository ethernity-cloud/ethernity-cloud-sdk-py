# syntax=docker/dockerfile:1
#
# Securelock enclave build. The whole build is SGX-free: heavy Python work
# (pip installs + PyInstaller freeze) runs on a STOCK python:3.14-alpine with
# no SCONE and no SGX; only the binary-fs packaging and signing use the SCONE
# build tools, which run in simulation/build mode without an SGX device.

########################################################################
# Stage 1 - SGX report helper (unchanged, crosscompiler build tool)
########################################################################
FROM registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/sconecuratedimages/crosscompilers:alpine-scone6.0.7 AS build-sgx-module
COPY src/get_sgx_report.c /etny-securelock/
RUN cd /etny-securelock/ && scone-gcc -shared -fPIC -O3 -o get_sgx_report.so get_sgx_report.c

########################################################################
# Stage 2 - PyInstaller freeze on a NORMAL python 3.14 (no SCONE / no SGX)
# Installs the user's requirements.txt + PyInstaller, renders securelock.py,
# and runs pyinstaller to produce the frozen COLLECT dir. Same python
# version + musl as the SCONE runtime python, so the output is compatible.
########################################################################
FROM python:3.14-alpine AS pyfreeze
RUN apk add --no-cache bash gcc g++ musl-dev libffi-dev openssl-dev rust cargo make libsodium-dev zlib-dev binutils
ENV SODIUM_INSTALL=system
WORKDIR /etny-securelock

# Core securelock runtime deps + PyInstaller (built here, off-SCONE, where pip
# and native builds work normally).
RUN pip install --no-cache-dir \
      pyinstaller \
      python-dotenv "web3>=6" cryptography ecdsa pyasn1 tinyec minio pynacl

# User-supplied additional modules (the customization hook).
COPY ./src/serverless/requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt || true

COPY ./src /etny-securelock/
COPY ./scripts/* /etny-securelock/
COPY --from=build-sgx-module /etny-securelock/get_sgx_report.so /etny-securelock/get_sgx_report.so

# Render securelock.py from the template, then run PyInstaller (off-SCONE).
ENV SECURELOCK_SESSION=__SECURELOCK_SESSION__
ENV BUCKET_NAME=__BUCKET_NAME__
ENV SMART_CONTRACT_ADDRESS=__SMART_CONTRACT_ADDRESS__
ENV IMAGE_REGISTRY_ADDRESS=__IMAGE_REGISTRY_ADDRESS__
ENV RPC_URL=__RPC_URL__
ENV CHAIN_ID=__CHAIN_ID__
ENV TRUSTED_ZONE_IMAGE=__TRUSTED_ZONE_IMAGE__
ENV NETWORK_TYPE=__NETWORK_TYPE__
RUN chmod +x /etny-securelock/pyfreeze.sh && /etny-securelock/pyfreeze.sh

########################################################################
# Stage 3 - binary-fs packaging with the SCONE build tools (no SGX).
# Assembles the SCONE python + stdlib + the (off-SCONE) installed site-packages
# + the rendered securelock.py + PyInstaller's collected .so list, and bakes
# them into the measured binary-fs.
########################################################################
FROM registry.ethernity.cloud:443/debuggingdelight/ethernity-cloud-sdk-registry/sconecuratedimages/crosscompilers:alpine-scone6.0.7 AS binfs
RUN mkdir -p /binary-fs-dir /etny-securelock
# SCONE-built static python + its stdlib, from the runtime base image
COPY --from=etny-securelock-serverless /usr/local/bin/python3 /usr/local/bin/python3
COPY --from=etny-securelock-serverless /usr/local/lib/python3.14 /usr/local/lib/python3.14
# The ENTIRE /etny-securelock tree (rendered securelock.py + its sibling modules
# etny_crypto.py, etny_exec.py, models.py, swift_stream_service.py, the abi/
# dir, get_sgx_report.so, scripts) - all must be baked into binary-fs or the
# enclave fails at import (ModuleNotFoundError: No module named 'etny_crypto').
# site-packages + COLLECT toc also come from the OFF-SCONE pyfreeze stage.
COPY --from=pyfreeze /etny-securelock /etny-securelock
COPY --from=pyfreeze /etny-securelock/build/securelock/COLLECT-00.toc /etny-securelock/COLLECT-00.toc
COPY --from=pyfreeze /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
RUN chmod +x /etny-securelock/binary-fs-build.sh && /etny-securelock/binary-fs-build.sh
RUN cd /binary-fs-dir && scone gcc ./binary_fs_blob.s ./libbinary_fs_template.a -shared -o /libbinary-fs.so

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
WORKDIR /etny-securelock

# Disabled production mode for testnet
__SCONE_SIGN__

RUN rm -rf /enclave-key.pem

ENTRYPOINT ["/usr/local/bin/python", "/etny-securelock/securelock.py"]
