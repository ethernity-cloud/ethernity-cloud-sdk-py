#!/bin/bash

cd /etny-securelock

echo "ENCLAVE_NAME_SECURE_LOCK = ${ENCLAVE_NAME_SECURELOCK}"

cat securelock.py.tmpl | sed  s/"__ENCLAVE_NAME_SECURELOCK__"/"${ENCLAVE_NAME_SECURELOCK}"/g > securelock.py.tmp
sed -i "s/__BUCKET_NAME__/${BUCKET_NAME}/g" securelock.py.tmp
sed -i "s/__SMART_CONTRACT_ADDRESS__/${SMART_CONTRACT_ADDRESS}/g" securelock.py.tmp
sed -i "s/__IMAGE_REGISTRY_ADDRESS__/${IMAGE_REGISTRY_ADDRESS}/g" securelock.py.tmp
sed -i "s/__RPC_URL__/${RPC_URL}/g" securelock.py.tmp
sed -i "s/__CHAIN_ID__/${CHAIN_ID}/g" securelock.py.tmp
sed -i "s/__TRUSTED_ZONE_IMAGE__/${TRUSTED_ZONE_IMAGE}/g" securelock.py.tmp
mv securelock.py.tmp securelock.py

pyinstaller securelock.py

EXEC=(scone binary-fs / /binary-fs-dir -v \
  --include '/usr/lib/libstdc++.so' \
  --include '/usr/lib/libstdc++.so.6' \
  --include '/usr/lib/libstdc++.so.6.0.28' \
  --include '/usr/lib/libgcc_s.so' \
  --include '/usr/lib/libgcc_s.so.1' \
  --include '/usr/lib/libgomp.so.1' \
  --include '/usr/lib/libgomp.so.1.0.0' \
  --include '/usr/lib/libopenblas.so' \
  --include '/usr/lib/libopenblas.so.3' \
  --include '/usr/lib/libopenblasp-r0.3.18.so' \
  --include '/usr/lib/libopenblas64_.so' \
  --include '/usr/lib/libopenblas64_.so.3' \
  --include '/usr/lib/libopenblas64_p-r0.3.18.so' \
  --include '/usr/lib/libgfortran.so' \
  --include '/usr/lib/libgfortran.so.5' \
  --include '/usr/lib/libgfortran.so.5.0.0' \
  --include '/usr/lib/libquadmath.so' \
  --include '/usr/lib/libquadmath.so.0' \
  --include '/usr/lib/libquadmath.so.0.0.0' \
  --include '/usr/local/lib/python3.10/*' \
  --include '/etny-securelock/*' \
  --host-path=/etc/resolv.conf \
  --host-path=/etc/hosts)


for FILE in `cat ./build/securelock/COLLECT-00.toc | grep '.so' | grep BINARY | awk -F "'" '{print $4}'`
do
  EXEC+=(--include "${FILE}"'*')
done

rm -rf build dist securelock.spec

echo "${EXEC[@]}"

SCONE_MODE=auto
exec "${EXEC[@]}"

exit
