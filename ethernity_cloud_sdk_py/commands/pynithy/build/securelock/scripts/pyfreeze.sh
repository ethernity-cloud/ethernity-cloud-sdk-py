#!/bin/bash
# Runs on a STOCK python:3.14-alpine (NO SCONE, NO SGX), after pip installed
# the securelock's packages and the dApp's requirements. Renders securelock.py
# from its template and compiles the bytecode of site-packages, which
# enclave-root.sh then copies into the enclave's root: the enclave runs the
# plain securelock.py with the SCONE python.
set -e
cd /etny-securelock

echo "SECURELOCK_SESSION = ${SECURELOCK_SESSION}"

# Use '|' as the sed delimiter so values containing '/' (e.g. the RPC URL
# https://...) don't break the substitution ("sed: bad option in substitution").
cp securelock.py.tmpl securelock.py.tmp
sed -i "s|__SECURELOCK_SESSION__|${SECURELOCK_SESSION}|g" securelock.py.tmp
sed -i "s|__BUCKET_NAME__|${BUCKET_NAME}|g" securelock.py.tmp
sed -i "s|__SMART_CONTRACT_ADDRESS__|${SMART_CONTRACT_ADDRESS}|g" securelock.py.tmp
sed -i "s|__IMAGE_REGISTRY_ADDRESS__|${IMAGE_REGISTRY_ADDRESS}|g" securelock.py.tmp
sed -i "s|__RPC_URL__|${RPC_URL}|g" securelock.py.tmp
sed -i "s|__CHAIN_ID__|${CHAIN_ID}|g" securelock.py.tmp
sed -i "s|__TRUSTED_ZONE_IMAGE__|${TRUSTED_ZONE_IMAGE}|g" securelock.py.tmp
sed -i "s|__NETWORK_TYPE__|${NETWORK_TYPE}|g" securelock.py.tmp
sed -i "s|__VALIDATOR_REGISTRY_ADDRESS__|${VALIDATOR_REGISTRY_ADDRESS}|g" securelock.py.tmp
mv securelock.py.tmp securelock.py

# The binary-fs holds site-packages with its bytecode, which pip writes with
# the time of the build in each file's header. Every __pycache__ of
# site-packages and of /etny-securelock is removed, and site-packages is
# compiled again in one process, in compileall's order, with headers that
# carry the hash of the source and that the interpreter does not check
# against it (unchecked-hash): two builds of the same sources write the same
# files. -f rewrites the bytecode the compiling interpreter writes at its own
# start, which compileall otherwise keeps as up to date. A file that does not
# compile keeps no bytecode, as with pip.
find /usr/local/lib/python3.14/site-packages /etny-securelock -depth -type d -name __pycache__ -exec rm -rf {} \;
python -m compileall -q -f --invalidation-mode unchecked-hash /usr/local/lib/python3.14/site-packages \
  || echo "compileall: a file of site-packages does not compile and keeps no bytecode"

echo "pyfreeze complete: securelock.py and site-packages ready for the enclave's root"
