#!/usr/bin/env bash
# Acceptance test for the unattended CLI (ESR plan Phase 2).
#
# Every step runs with no TTY and must either succeed or fail with an
# actionable error — never hang on a prompt.
#
# Requirements: bash + python3 + the SDK's dependencies. No docker daemon, no
# network, no wallet, no SGX — nothing is built or published.
#
# From a checkout (deps already installed):
#   bash tests/unattended_acceptance.sh
#
# Inside a container on any runner (installs deps itself):
#   docker run --rm -v "$PWD":/sdk -w /sdk python:3.11-slim \
#     bash -c "pip install -q -e . && bash tests/unattended_acceptance.sh"
#
# Exits 0 when every check passes, non-zero (with the failing step named) on
# the first regression — suitable as a release gate or a CI step anywhere.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

export PYTHONPATH="$REPO_ROOT"
export PYTHONIOENCODING=utf-8

# Interpreter selection. ECLD_TEST_PYTHON wins (Windows callers pass their
# venv python, since `bash` there may be WSL whose Linux python is a different
# environment entirely). Otherwise prefer python3 — slim/alpine images often
# ship only that name.
PY="${ECLD_TEST_PYTHON:-$(command -v python3 || command -v python || true)}"
[ -n "$PY" ] || { echo "FAIL: no python3 on PATH"; exit 1; }

# Preflight: the SDK AND its runtime dependencies must be importable. Import a
# module that pulls the dependency chain (dotenv, web3, ...) rather than just
# the package root, so a partially-provisioned interpreter is caught here with
# an actionable message instead of failing mid-suite.
if ! "$PY" -c "import ethernity_cloud_sdk_py.commands.init" > /dev/null 2>&1; then
  echo "FAIL: the SDK or its dependencies are not importable by:"
  echo "        $PY"
  echo "      Install them there:  $PY -m pip install -e $REPO_ROOT"
  echo "      (On Windows, bash may be WSL. Point the suite at your venv with"
  echo "       ECLD_TEST_PYTHON=/c/path/to/venv/Scripts/python.exe)"
  exit 1
fi

py_cli() { # py_cli <entrypoint> [args...] — runs a console entrypoint promptless
  local entry="$1"; shift
  "$PY" -c "
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
"$PY" -c "
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
"$PY" - <<'PY'
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
"$PY" -c "
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

step "5. ESR identity wallet (derivation + address capture)"
PYTHONIOENCODING=utf-8 "$PY" - <<'PY'
import os, sys
src = os.path.join(os.environ["PYTHONPATH"],
                   "ethernity_cloud_sdk_py", "commands", "pynithy",
                   "build", "securelock", "src")
sys.path.insert(0, src)
import esr_wallet as w

k1 = b"identity-key-one"
k2 = b"identity-key-two"
a1 = w.derive_wallet_address(k1)
assert a1 == w.derive_wallet_address(k1), "derivation must be deterministic"
assert a1 != w.derive_wallet_address(k2), "wallet must be bound to the identity key"
assert a1.startswith("0x") and len(a1) == 42, a1
assert a1 != a1.lower(), "address must be EIP-55 checksummed"

import hashlib
assert w._derive_wallet_private_key(k1) != hashlib.sha256(k1).digest(), \
    "domain separation missing"
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
assert 0 < int.from_bytes(w._derive_wallet_private_key(k1), "big") < N

from eth_account import Account
assert Account.from_key(w._derive_wallet_private_key(k1)).address == a1, \
    "address must match the derived key"

# The key derivation must stay OUT of the public surface: the client payload is
# exec'd inside the securelock image and can import this module, so exporting it
# would hand untrusted code a ready-made way to materialise the wallet key.
assert "derive_wallet_private_key" not in getattr(w, "__all__", []), \
    "the private-key derivation must not be in __all__"
assert not hasattr(w, "derive_wallet_private_key"), \
    "the private-key derivation must not be public"

assert w.is_secret_identity("mainnet") and not w.is_secret_identity("testnet")
try:
    w.derive_wallet_address(b""); sys.exit("empty identity key must raise")
except ValueError:
    pass

# publish-side capture: only the ADDRESS is ever consumed, and only when ESR
# is enabled.
from ethernity_cloud_sdk_py.commands.pynithy import publish as P
out = f"INFO - ESR_WALLET_ADDRESS: {a1}\n"
assert P._capture_esr_wallet_address(out) == a1
assert P.config.read_esr()["wallet_address"] == a1
assert P._capture_esr_wallet_address("INFO - no address here") is None

esr = P.config.read_esr(); esr["enabled"] = False; P.config.write_esr(esr)
assert P._capture_esr_wallet_address(out) is None, "disabled ESR must ignore the address"
esr["enabled"] = True; esr["wallet_address"] = ""; P.config.write_esr(esr)
print("ESR wallet derivation + capture OK")
PY
echo "ESR identity wallet OK"

step "6. ESR publish summary: unattended funding parameters"
"$PY" - <<'PY'
import sys, os, io as _io, ast
src = open(os.path.join(os.environ["PYTHONPATH"],
    "ethernity_cloud_sdk_py", "commands", "pynithy", "publish.py"),
    encoding="utf-8").read()
fn = next(n for n in ast.parse(src).body
          if isinstance(n, ast.FunctionDef) and n.name == "_esr_summary")
fn_src = ast.get_source_segment(src, fn)

class Cfg:
    def __init__(s, e): s.e = e
    def read_esr(s): return s.e
class Reg:
    class acct: address = "0xDEV"
    def get_balance_of(s, a): return 0
    def transfer_native(s, a, amt): return f"0xTX({amt})"
class OsStub:
    def __init__(s, env): s.env = env
    def getenv(s, k, d=""): return s.env.get(k, d)

A = "0x1286655050bA374F24BAc576673318E35DcFb23d"
def run(env, ni=True):
    ns = {"config": Cfg({"enabled": True, "wallet_address": A}),
          "image_registry": Reg(), "non_interactive": lambda: ni,
          "os": OsStub(env)}
    exec(fn_src, ns)
    # stdin is EMPTY: any attempt to prompt raises EOFError and fails the step
    old, sys.stdin = sys.stdin, _io.StringIO("")
    buf, out = _io.StringIO(), sys.stdout
    sys.stdout = buf
    try: ns["_esr_summary"]()
    finally: sys.stdout, sys.stdin = out, old
    return buf.getvalue()

# amount + ceiling -> sends, promptless (works even when a TTY is attached)
assert "0xTX(0.3)" in run({"ECLD_ESR_FUND_AMOUNT": "0.3", "ECLD_ESR_FUND_MAX": "1"}, ni=False)
# amount without ceiling -> refused (autofund's explicit-ceiling rule)
o = run({"ECLD_ESR_FUND_AMOUNT": "0.3"})
assert "ECLD_ESR_FUND_MAX" in o and "0xTX" not in o
# over ceiling -> refused
assert "0xTX" not in run({"ECLD_ESR_FUND_AMOUNT": "2", "ECLD_ESR_FUND_MAX": "1"})
# skip -> suppressed, promptless
assert "0xTX" not in run({"ECLD_ESR_FUND_AMOUNT": "skip"}, ni=False)
# unattended with nothing set -> instructions, no prompt, nothing sent
o = run({})
assert "0xTX" not in o and "ECLD_ESR_FUND_AMOUNT" in o
print("ESR funding parameters OK")
PY
echo "ESR unattended funding OK"

echo
echo "ALL UNATTENDED ACCEPTANCE CHECKS PASSED"
