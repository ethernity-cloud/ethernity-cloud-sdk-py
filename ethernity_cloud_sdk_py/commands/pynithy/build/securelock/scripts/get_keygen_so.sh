#!/usr/bin/env bash
# Refresh the prebuilt SGX key-gen module the SDK ships.
#
# get_sgx_report.so is a build artifact of the etny-pynithy / etny-nodenithy
# pipelines (they cross-compile get_sgx_report.c with scone-gcc). This copies
# that .so into the SDK; commit src/get_sgx_report.so afterwards.
#
#   bash scripts/get_keygen_so.sh /path/to/pynithy/build/output/get_sgx_report.so
#
# or point it at a pynithy checkout that already built the .so:
#   bash scripts/get_keygen_so.sh /path/to/etny-pynithy
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"   # .../securelock
DEST="$HERE/src/get_sgx_report.so"
ARG="${1:-}"

if [ -z "$ARG" ]; then
  echo "usage: get_keygen_so.sh <path-to-get_sgx_report.so | path-to-etny-pynithy>"
  exit 2
fi

SRC="$ARG"
if [ -d "$ARG" ]; then
  # search a pynithy checkout / build tree for the compiled .so
  SRC="$(find "$ARG" -name get_sgx_report.so -type f 2>/dev/null | head -1 || true)"
  [ -n "$SRC" ] || { echo "ERROR: no get_sgx_report.so found under $ARG (build pynithy first)"; exit 1; }
fi
[ -f "$SRC" ] || { echo "ERROR: $SRC is not a file"; exit 1; }

# Sanity: it must be an ELF shared object exporting the expected symbols.
if command -v nm >/dev/null 2>&1; then
  if ! nm -D "$SRC" 2>/dev/null | grep -q derive_identity_scalar; then
    echo "ERROR: $SRC does not export derive_identity_scalar -- wrong/old build?"
    exit 1
  fi
fi

cp "$SRC" "$DEST"
echo "[keygen] copied $SRC -> $DEST"
echo "[keygen] leaked tag strings (should be none):"
strings "$DEST" 2>/dev/null | grep -iE "identity/v2|ethernity-cloud/" || echo "  (none)"
echo "[keygen] commit src/get_sgx_report.so"
