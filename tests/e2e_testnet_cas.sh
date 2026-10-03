#!/usr/bin/env bash
# End-to-end on the CAS-attested testnet: build a securelock with this SDK,
# publish it (session registered ON-CHAIN, certificate provisioned by the
# ethernity-cas validator set), run one task on the network, and check that
# a CAS validator recorded a verdict for the order.
#
# What it proves, in order:
#   1. ecld-init / ecld-build produce a production-signed securelock for
#      BLOXBERG_TESTNET, the CAS-attested testnet.
#   2. ecld-publish registers its session in the SessionRegistry, the
#      validator set serves it, the enclave's certificate comes out of a CAS
#      session and is registered in the ImageRegistry.
#   3. ecld-run places an order that a node processes with THIS securelock
#      and the network's trustedzone, both provisioned by the CAS; the task
#      result comes back.
#   4. The ValidatorRegistry holds a validation for that order (the CAS
#      order sweep reproduced the result); its settlement on PoX follows the
#      challenge window and is reported, not waited for.
#
# Requirements: docker with SGX (/dev/sgx_enclave), a login for
# registry.scontain.com, the SDK and ethernity-cloud-runner-py installed,
# bash + python3. It spends testnet gas and tETNY.
#
# Env:
#   ECLD_PRIVATE_KEY      funded bloxberg testnet wallet (0x-prefixed); owns
#                         the project's ImageRegistry entry and its session
#   E2E_PROJECT           project name, default cas-e2e (the session name is
#                         derived from it; the first publish of a name fixes
#                         its creator wallet)
#   E2E_WORKDIR           where the project is scaffolded, default ./cas-e2e-work
#   E2E_SKIP_BUILD        set to 1 to reuse the securelock image a previous run
#                         built in E2E_WORKDIR (publish then skips the session
#                         registration too, since the MRENCLAVE is unchanged)
#   E2E_NODE              node operator address to pin the order to; unset lets
#                         any node on the network take it, including one whose
#                         platform the CAS refuses
#   E2E_TASK_PRICE        tETNY offered per task, default 3
#   E2E_VERDICT_TIMEOUT   seconds to wait for the CAS verdict, default 1200
#   VALIDATOR_REGISTRY    default 0xa821b36F378F76c793c436F5f9c9CC36c684eBE5
#   ECLD_CAS_ADDR         optional; names the CAS instead of resolving one
set -euo pipefail

[ -n "${ECLD_PRIVATE_KEY:-}" ] || { echo "FAIL: ECLD_PRIVATE_KEY is required"; exit 1; }

PROJECT="${E2E_PROJECT:-cas-e2e}"
WORK="${E2E_WORKDIR:-$PWD/cas-e2e-work}"
TASK_PRICE="${E2E_TASK_PRICE:-3}"
VERDICT_TIMEOUT="${E2E_VERDICT_TIMEOUT:-1200}"
VALIDATOR_REGISTRY="${VALIDATOR_REGISTRY:-0xa821b36F378F76c793c436F5f9c9CC36c684eBE5}"
RPC="${ETNY_WEB3_PROVIDER:-https://bloxberg.ethernity.cloud}"
PY="${ECLD_TEST_PYTHON:-$(command -v python3 || command -v python)}"

export ECLD_NON_INTERACTIVE=1 ECLD_ASSUME_YES=1 PYTHONIOENCODING=utf-8

step() { echo; echo "=== $* ==="; }
fail() { echo "FAIL: $*"; exit 1; }

mkdir -p "$WORK"
cd "$WORK"

step "1. init $PROJECT for BLOXBERG_TESTNET"
if [ ! -f .config.json ]; then
  ECLD_PROJECT_NAME="$PROJECT" ECLD_BLOCKCHAIN_NETWORK=BLOXBERG_TESTNET \
  ECLD_DAPP_TYPE=Pynithy ECLD_APP_TEMPLATE=yes ECLD_ESR_ENABLE=false \
    ecld-init < /dev/null
fi
"$PY" - <<'PY'
import json
c = json.load(open(".config.json"))
assert c["BLOCKCHAIN_NETWORK"] == "BLOXBERG_TESTNET", c
print("project", c["PROJECT_NAME"], "on", c["BLOCKCHAIN_NETWORK"])
PY

step "2. build the securelock (production-signed, CAS-provisioned)"
if [ "${E2E_SKIP_BUILD:-0}" = "1" ] && docker image inspect localhost:5000/etny-securelock:latest >/dev/null 2>&1; then
  echo "reusing the securelock image built by a previous run (E2E_SKIP_BUILD=1)"
else
  ECLD_MEMORY_TO_ALLOCATE="${ECLD_MEMORY_TO_ALLOCATE:-1GB}" ecld-build < /dev/null
fi

step "3. publish: on-chain session, CAS-provisioned certificate, ImageRegistry"
ECLD_REMOTE_CERT_EXTRACTION=never ecld-publish < /dev/null
"$PY" - <<'PY'
import json
c = json.load(open(".config.json"))
for k in ("IPFS_HASH", "MRENCLAVE_SECURELOCK", "SECURELOCK_SESSION"):
    assert c.get(k), f"{k} missing after publish"
print("securelock", c["SECURELOCK_SESSION"], "image", c["IPFS_HASH"], "mrenclave", c["MRENCLAVE_SECURELOCK"])
PY

step "4. run one task on the network${E2E_NODE:+ (node $E2E_NODE)}"
ecld-run --json --task-price "$TASK_PRICE" ${E2E_NODE:+--node "$E2E_NODE"} 'hello("CAS")' > run.json < /dev/null
ORDER_ID="$("$PY" -c 'import json; r=json.load(open("run.json")); print(r["order_id"])')"
"$PY" - <<'PY'
import json
r = json.load(open("run.json"))
assert r.get("task_code_int") in (None, 0), r
assert "Hello CAS" in str(r.get("value", "")), r
print("order", r["order_id"], "result", r.get("value"))
PY

step "5. the CAS validators' verdict for order $ORDER_ID"
VALIDATOR_REGISTRY="$VALIDATOR_REGISTRY" RPC="$RPC" ORDER_ID="$ORDER_ID" TIMEOUT="$VERDICT_TIMEOUT" "$PY" - <<'PY'
import os, sys, time
from web3 import Web3
abi = [{"name": "validations", "type": "function", "stateMutability": "view",
        "inputs": [{"type": "uint256"}],
        "outputs": [{"name": "validator", "type": "address"}, {"name": "valid", "type": "bool"},
                    {"name": "votedBlock", "type": "uint64"}, {"name": "challenged", "type": "bool"},
                    {"name": "challenger", "type": "address"}, {"name": "challengedBlock", "type": "uint64"},
                    {"name": "against", "type": "uint32"}, {"name": "forOriginal", "type": "uint32"},
                    {"name": "resolved", "type": "bool"}]},
       {"name": "challengeWindowBlocks", "type": "function", "stateMutability": "view",
        "inputs": [], "outputs": [{"type": "uint64"}]}]
w3 = Web3(Web3.HTTPProvider(os.environ["RPC"]))
reg = w3.eth.contract(address=Web3.to_checksum_address(os.environ["VALIDATOR_REGISTRY"]), abi=abi)
order = int(os.environ["ORDER_ID"]); deadline = time.time() + int(os.environ["TIMEOUT"])
while True:
    v = reg.functions.validations(order).call()
    if int(v[0], 16) != 0:
        break
    if time.time() > deadline:
        sys.exit(f"no CAS verdict for order {order} within {os.environ['TIMEOUT']}s")
    time.sleep(20)
window = reg.functions.challengeWindowBlocks().call()
print(f"order {order}: validator {v[0]} voted valid={v[1]} at block {v[2]}; "
      f"settlement on PoX opens after block {v[2] + window} (challenge window {window} blocks)")
assert v[1], "the CAS judged the result INVALID"
PY

echo
echo "E2E PASSED: securelock built, published through the CAS, ran on the network, and was validated by the CAS"
