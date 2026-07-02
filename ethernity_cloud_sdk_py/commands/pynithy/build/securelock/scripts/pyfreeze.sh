#!/bin/bash
# Runs on a STOCK python:3.14-alpine (NO SCONE, NO SGX). Renders securelock.py
# from its template and runs PyInstaller purely as a dependency-analysis pass:
# PyInstaller resolves every module/shared-object securelock needs and records
# them in build/securelock/COLLECT-00.toc. The frozen executable itself is not
# used at runtime (the enclave runs the plain securelock.py with the SCONE
# python); we only need the collected .so list + the installed site-packages,
# which the later SCONE stage bakes into the measured binary-fs.
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
mv securelock.py.tmp securelock.py

# Dependency-analysis freeze (off-SCONE). --collect-all pulls in data/binaries
# for the crypto/web3 stack so the COLLECT toc is complete.
pyinstaller --onedir --noconfirm securelock.py

echo "pyfreeze complete: COLLECT-00.toc + site-packages ready for binary-fs"
