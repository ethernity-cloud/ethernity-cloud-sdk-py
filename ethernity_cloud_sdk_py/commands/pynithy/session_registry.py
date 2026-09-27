"""Register a securelock session ON-CHAIN, in the ethernity-cas SessionRegistry.

On a CAS-attested testnet (the bloxberg testnet) the CAS is a validator set
that reads its sessions from the chain and never accepts a POST: the
publisher pins the session body
to IPFS and submits `SessionRegistry.register` itself, with the same wallet
that owns the ImageRegistry entry. The first registration of a name fixes its
creator; later versions of the same name must come from that wallet.

The body is pinned as CIDv1/raw/sha2-256, so with hashAlgo 1 the on-chain
sessionHash and the CID commit to the same digest (sha256 of the exact
bytes) -- what lets a validator check the body it fetched against the chain
without trusting the gateway.

The same recipe as etny-pynithy's `v3/run/register_session.py`, which
registers the trustedzone session from CI.
"""
import hashlib
import json
import os
import time

import requests
from web3 import Web3

try:
    from web3.middleware import ExtraDataToPOAMiddleware as _poa_middleware
except Exception:
    from web3.middleware import geth_poa_middleware as _poa_middleware

ABI = json.loads("""[
 {"type":"function","name":"register","stateMutability":"nonpayable",
  "inputs":[{"name":"sessionHash","type":"bytes32"},{"name":"name","type":"string"},
            {"name":"bodyCid","type":"string"},
            {"name":"imageCid","type":"string"},{"name":"hashAlgo","type":"uint8"},
            {"name":"rulesIn","type":"tuple","components":[
               {"name":"tolerate","type":"string[]"},
               {"name":"ignoreAdvisories","type":"string[]"},
               {"name":"mrenclaves","type":"string[]"},
               {"name":"minIsvSvn","type":"uint16"},
               {"name":"debugAllowed","type":"bool"},
               {"name":"skipQuoteVerification","type":"bool"}]}],
  "outputs":[{"name":"version","type":"uint32"}]},
 {"type":"function","name":"latest","stateMutability":"view",
  "inputs":[{"name":"name","type":"string"}],"outputs":[{"type":"bytes32"}]},
 {"type":"function","name":"linkImage","stateMutability":"nonpayable",
  "inputs":[{"name":"sessionHash","type":"bytes32"},
            {"name":"imageCid","type":"string"}],"outputs":[]}
]""")

HASH_ALGO_SHA256 = 1


def _yaml_list(text):
    text = text.strip()
    if text.startswith("["):
        inner = text[1:text.find("]")] if "]" in text else text[1:]
        return [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
    return [x.strip().strip('"').strip("'") for x in text.split(",") if x.strip()]


def parse_name_and_rules(text):
    """The session's name and the SessionRules tuple the registry stores
    beside it: (tolerate, ignoreAdvisories, mrenclaves, minIsvSvn,
    debugAllowed, skipQuoteVerification)."""
    name = ""
    tolerate, ignore_adv, mrenclaves = [], [], []
    min_isvsvn, debug_allowed, skip_quote = 0, False, False
    for raw in text.splitlines():
        line = raw.strip()
        if not name and line.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("tolerate:"):
            tolerate = _yaml_list(line.split(":", 1)[1])
            debug_allowed = any("debug" in t.lower() for t in tolerate)
        elif line.startswith("ignore_advisories:"):
            ignore_adv = _yaml_list(line.split(":", 1)[1])
        elif line.startswith("mrenclaves:"):
            for m in _yaml_list(line.split(":", 1)[1]):
                if m and m not in mrenclaves:
                    mrenclaves.append(m)
        elif line.startswith("isvsvn:") or line.startswith("min_isvsvn:"):
            try:
                min_isvsvn = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("skip_quote_verification:"):
            skip_quote = line.split(":", 1)[1].strip().strip('"').strip("'") == "true"
    return name, (tolerate, ignore_adv, mrenclaves, min_isvsvn, debug_allowed, skip_quote)


def session_hash(body):
    return hashlib.sha256(body).digest()


def pin_body(ipfs_api_url, body):
    """Pin the exact bytes as CIDv1/raw/sha2-256 and return the CID."""
    r = requests.post(
        f"{ipfs_api_url}/api/v0/add?cid-version=1&raw-leaves=true&pin=true",
        files={"file": ("session", body)}, timeout=60)
    r.raise_for_status()
    return r.json()["Hash"]


def _web3(provider_url):
    # Bounded: a stalled RPC must fail the step, not hang the publish.
    w3 = Web3(Web3.HTTPProvider(provider_url, request_kwargs={"timeout": 30}))
    try:
        w3.middleware_onion.inject(_poa_middleware, layer=0)
    except Exception:
        pass
    return w3


def _send(w3, chain_id, key, fn, gas):
    acct = w3.eth.account.from_key(key)
    txn = fn.build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
        "chainId": chain_id, "gas": gas, "gasPrice": w3.to_wei("1", "mwei"),
    })
    signed = w3.eth.account.sign_transaction(txn, private_key=key)
    raw = getattr(signed, "raw_transaction", None)
    if raw is None:
        raw = signed.rawTransaction
    txh = w3.eth.send_raw_transaction(raw)
    return txh, w3.eth.wait_for_transaction_receipt(txh, timeout=180)


def register(provider_url, chain_id, registry_address, key, body, ipfs_api_url, image_cid=""):
    """Pin `body` and register it under its own `name:`. Returns
    (name, hash_hex, cid, registered): `registered` is False when the chain
    already held these exact bytes as the name's latest version, which the
    contract would refuse as a duplicate."""
    text = body.decode("utf-8", "replace")
    name, rules = parse_name_and_rules(text)
    if not name:
        raise SystemExit("the session body has no `name:`")
    digest = session_hash(body)
    w3 = _web3(provider_url)
    reg = w3.eth.contract(address=Web3.to_checksum_address(registry_address), abi=ABI)
    cid = pin_body(ipfs_api_url, body)
    if reg.functions.latest(name).call() == digest:
        return name, digest.hex(), cid, False
    txh, rcpt = _send(w3, chain_id, key,
                      reg.functions.register(digest, name, cid, image_cid, HASH_ALGO_SHA256, rules),
                      gas=800000)
    if rcpt.status != 1:
        raise SystemExit(
            f"SessionRegistry.register reverted for {name} (tx {txh.hex()}): this name's "
            f"creator may be another wallet, or the SessionRules shape differs from the ABI")
    return name, digest.hex(), cid, True


def link_image(provider_url, chain_id, registry_address, key, name, image_cid):
    """Point the name's latest version at the published image CID."""
    w3 = _web3(provider_url)
    reg = w3.eth.contract(address=Web3.to_checksum_address(registry_address), abi=ABI)
    digest = reg.functions.latest(name).call()
    if not any(digest):
        raise SystemExit(f"no registered session named {name!r}")
    txh, rcpt = _send(w3, chain_id, key, reg.functions.linkImage(digest, image_cid), gas=300000)
    if rcpt.status != 1:
        raise SystemExit(f"SessionRegistry.linkImage reverted for {name} (tx {txh.hex()})")


def wait_visible(provider_url, registry_address, name, hash_hex, timeout=180, poll_secs=None):
    """Block until the chain reports `name` at `hash_hex`, then hold while the
    validators pick it up.

    The hold is three legs, each up to one CAS poll interval
    (ECAS_SESSION_WATCH_SECS, 60 s): the elected writer's poll notices the
    registration and generates the session's secrets, its recordKey
    transaction confirms, and the other validators' next poll adopts the
    recorded blob. The enclave cannot retry and the validator it dials is the
    node's choice, so the wait covers the LAST validator to adopt. Measured
    on bloxberg: registered 10:44:00, writer recorded 10:44:41, last member
    adopted 10:45:20."""
    w3 = _web3(provider_url)
    reg = w3.eth.contract(address=Web3.to_checksum_address(registry_address), abi=ABI)
    want = hash_hex.lower().removeprefix("0x")
    deadline = time.time() + timeout
    while True:
        try:
            got = reg.functions.latest(name).call().hex().removeprefix("0x")
        except Exception as e:
            got = f"(read failed: {e})"
        if got == want:
            print(f"\t✔  session {name} visible on chain at 0x{got}", flush=True)
            break
        if time.time() >= deadline:
            raise SystemExit(
                f"session {name} did not become visible within {timeout}s "
                f"(chain reports {got}, expected {want})")
        time.sleep(5)
    poll = poll_secs if poll_secs is not None else int(os.environ.get("CAS_SESSION_POLL_SECS", "60"))
    hold = 2 * poll + 60
    print(f"\t   holding {hold}s for the CAS validators to generate and mirror it", flush=True)
    time.sleep(hold)
