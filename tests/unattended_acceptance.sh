#!/usr/bin/env bash
# Acceptance test for the unattended CLI (ESR plan Phase 2).
#
# Every step runs with no TTY and must either succeed or fail with an
# actionable error — never hang on a prompt. Run it from the repo root:
#
#   bash tests/unattended_acceptance.sh
#
# It needs only Python + the repo itself (no docker, no network, no wallet).
# The same checks exist as a GitHub Actions job in
# docs/ci/unattended-cli.yml.example — move that file to .github/workflows/
# (requires a workflow-scoped token) to enforce them on every PR.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

export PYTHONPATH="$REPO_ROOT"
export PYTHONIOENCODING=utf-8

py_cli() { # py_cli <entrypoint> [args...] — runs a console entrypoint promptless
  local entry="$1"; shift
  python -c "
import sys
sys.argv = ['$entry'] + [a for a in sys.argv[1:]]
from ethernity_cloud_sdk_py.cli import main_${entry#ecld-}
main_${entry#ecld-}()
" "$@" < /dev/null
}

fail() { echo "FAIL: $*"; exit 1; }
step() { echo; echo "=== $* ==="; }

step "1. unattended ecld-init (with ESR)"
ECLD_NON_INTERACTIVE=1 \
ECLD_PROJECT_NAME=ci_acceptance \
ECLD_BLOCKCHAIN_NETWORK=BLOXBERG_TESTNET \
ECLD_DAPP_TYPE=Pynithy \
ECLD_APP_TEMPLATE=yes \
ECLD_ESR_ENABLE=true \
ECLD_ESR_CONTRACT=0x1111111111111111111111111111111111111111 \
py_cli ecld-init > init.log 2>&1 || { cat init.log; fail "init exited non-zero"; }
python -c "
import json
c = json.load(open('.config.json'))
assert c['PROJECT_NAME'] == 'ci_acceptance', c
assert c['BLOCKCHAIN_NETWORK'] == 'BLOXBERG_TESTNET', c
assert c['ESR']['enabled'] is True, c
assert c['ESR']['contract_address'].startswith('0x1111'), c
print('init config OK')
"
test -f src/serverless/backend.py || fail "template not scaffolded"

step "2. ESR build gate fails fast on a bad address"
python - <<'PY'
import json, sys
c = json.load(open(".config.json")); c["ESR"]["contract_address"] = "oops"
json.dump(c, open(".config.json", "w"))
from ethernity_cloud_sdk_py.commands.pynithy import build
build.config.load()
try:
    build.validate_esr_config()
except SystemExit as e:
    assert e.code == 1, e.code
    print("gate fired correctly"); sys.exit(0)
sys.exit("gate did not fire")
PY
python -c "
import json
c = json.load(open('.config.json'))
c['ESR']['contract_address'] = '0x1111111111111111111111111111111111111111'
json.dump(c, open('.config.json', 'w'))
"

step "3. ecld-test: success, expect, env-driven, failure codes"
py_cli ecld-test --expect 'Hello World' 'hello("World")' > /dev/null
ECLD_TEST_CODE='hello("Env")' ECLD_TEST_EXPECT='Hello Env' py_cli ecld-test > /dev/null
if ECLD_TEST_EXPECT='wrong' py_cli ecld-test 'hello("World")' > /dev/null 2>&1; then
  fail "expected non-zero exit on expect mismatch"
fi
if py_cli ecld-test 'no_such_function()' > /dev/null 2>&1; then
  fail "expected non-zero exit on task failure"
fi
out=$(ESR_CONTRACT_ADDRESS="" py_cli ecld-test 'hello("x")' 2>&1 || true)
echo "$out" | grep -q "task code: 32" || { echo "$out"; fail "expected CONFIG_ERROR 32"; }
echo "ecld-test matrix OK"

step "4. unattended publish errors actionably without a key"
out=$(ECLD_NON_INTERACTIVE=1 py_cli ecld-publish 2>&1 || true)
echo "$out" | grep -q "ECLD_PRIVATE_KEY" || { echo "$out"; fail "expected actionable key error"; }
echo "publish guard OK"

echo
echo "ALL UNATTENDED ACCEPTANCE CHECKS PASSED"
