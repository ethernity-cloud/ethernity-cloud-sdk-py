#!/bin/bash -v

pip3 install pyinstaller
apk add binutils

cd /etny-securelock
pyinstaller etny_result.py

EXEC=(scone binaryfs / /binary-fs-dir -v \
  --include '/usr/local/lib/python3.10/*' \
  --include '/etny-securelock/*' \
  --host-path=/etc/resolv.conf \
  --host-path=/etc/hosts)


for FILE in `cat ./build/etny_result/COLLECT-00.toc | grep '.so' | grep BINARY | awk -F "'" '{print $4}'`
do
  EXEC+=(--include "${FILE}"'*')
done

rm -rf build dist etny_result.spec

echo "${EXEC[@]}"

SCONE_MODE=auto
exec "${EXEC[@]}"

exit
