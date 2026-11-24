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

The rules are posted in the form of the registry's `rules`, which the caller
names (enums.SESSION_RULES_FORM): 1, the flat form (one measurement list for
the session), or 2, the per-service form (the measurements of each service
of the body's `services:`), as ethernity-cloud-sdk-js cas/session_registry.js
posts them. Before the body is pinned, the registry's RULES_FORM() is read (a
revert is form 1, the registries of the flat form having none), and a
registry that states another form than the one named stops the
registration.
"""
import hashlib
import os
import re
import time
import warnings

import requests
from eth_abi import decode
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from ethernity_cloud_sdk_py.commands import fees

try:
    from web3.middleware import ExtraDataToPOAMiddleware as _poa_middleware
except Exception:
    from web3.middleware import geth_poa_middleware as _poa_middleware

# The forms of a SessionRegistry's `rules`, numbered as its RULES_FORM()
# numbers them.
FLAT_FORM = 1
PER_SERVICE_FORM = 2

# The measurements of the rules struct of each form: the flat form's lists
# the session's (`mrenclaves`), the per-service form's each service's
# (`services`, each a name and its measurements).
_MEASUREMENTS = {
    FLAT_FORM: {"name": "mrenclaves", "type": "string[]"},
    PER_SERVICE_FORM: {"name": "services", "type": "tuple[]", "components": [
        {"name": "name", "type": "string"}, {"name": "mrenclaves", "type": "string[]"}]},
}


def abi_of(form):
    """The SessionRegistry's ABI in rules form `form`. `latest` and `record`
    name a version by its record key: a record id (keccak256(abi.encode(name,
    sessionHash))), or the body's hash on the registries that key records by
    it. The record holds the body's hash on both. The calls of `latest`,
    `record`, `linkImage` and `RULES_FORM` are the same in both forms."""
    rules = {"name": "rulesIn", "type": "tuple", "components": [
        {"name": "tolerate", "type": "string[]"},
        {"name": "ignoreAdvisories", "type": "string[]"},
        _MEASUREMENTS[form],
        {"name": "minIsvSvn", "type": "uint16"},
        {"name": "debugAllowed", "type": "bool"},
        {"name": "skipQuoteVerification", "type": "bool"}]}
    return [
        {"type": "function", "name": "register", "stateMutability": "nonpayable",
         "inputs": [{"name": "sessionHash", "type": "bytes32"}, {"name": "name", "type": "string"},
                    {"name": "bodyCid", "type": "string"}, {"name": "imageCid", "type": "string"},
                    {"name": "hashAlgo", "type": "uint8"}, rules],
         "outputs": [{"name": "version", "type": "uint32"}]},
        {"type": "function", "name": "latest", "stateMutability": "view",
         "inputs": [{"name": "name", "type": "string"}], "outputs": [{"name": "", "type": "bytes32"}]},
        {"type": "function", "name": "record", "stateMutability": "view",
         "inputs": [{"name": "id", "type": "bytes32"}],
         "outputs": [{"name": "sessionHash", "type": "bytes32"}, {"name": "creator", "type": "address"},
                     {"name": "enclaveName", "type": "string"}, {"name": "version", "type": "uint32"},
                     {"name": "bodyCid", "type": "string"}, {"name": "imageCid", "type": "string"},
                     {"name": "hashAlgo", "type": "uint8"}, {"name": "registeredAt", "type": "uint64"},
                     {"name": "exists", "type": "bool"}]},
        {"type": "function", "name": "linkImage", "stateMutability": "nonpayable",
         "inputs": [{"name": "id", "type": "bytes32"}, {"name": "imageCid", "type": "string"}], "outputs": []},
        {"type": "function", "name": "RULES_FORM", "stateMutability": "view",
         "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
    ]


HASH_ALGO_SHA256 = 1

# The registry's custom errors, by name, with their argument types.
ERRORS = {
    "NotCreator": ["address", "address"],
    "DuplicateSession": ["bytes32"],
    "NotImageNameOwner": ["string", "address", "address"],
    "NotAllowedPublisher": ["address"],
    "AmbiguousSecurelockName": ["string"],
    "EmptyServiceName": ["uint256"],
    "DuplicateService": ["string"],
}


def _refusal(e):
    """A refusal as the registry states it: the custom error and its
    arguments when `e` carries one of the registry's errors, else its text.
    The bloxberg node (Parity) returns the revert data as "Reverted 0x...",
    and web3 keeps that prefix in `e.data`."""
    data = getattr(e, "data", None)
    if isinstance(data, str):
        data = data.removeprefix("Reverted ")
    if isinstance(data, str) and data.startswith("0x") and len(data) >= 10:
        for error, types in ERRORS.items():
            selector = Web3.keccak(text=f"{error}({','.join(types)})")[:4].hex().removeprefix("0x")
            if selector == data[2:10]:
                args = decode(types, bytes.fromhex(data[10:]))
                shown = ", ".join("0x" + a.hex() if isinstance(a, bytes) else str(a) for a in args)
                return f"{error}({shown})"
    return str(e)


def latest_record(reg, name):
    """The latest version of `name`: (record key, body hash, creator), or
    None when the name has none."""
    record_id = reg.functions.latest(name).call()
    if not any(record_id):
        return None
    rec = reg.functions.record(record_id).call()
    return record_id, rec[0], rec[1]


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


def _unquote(text):
    """`text` without one leading and one trailing quote, single or double."""
    return re.sub(r"^[\"']|[\"']$", "", text)


def parse_services(text):
    """The services of the body's top-level `services:` list, in order, each
    (name, measurements): the `name:` of its entry and the measurements of its
    `mrenclaves:` line, an inline list as parse_name_and_rules reads one,
    each kept once. A service without that line admits none. A key of an
    entry is one at the column of the entry's first key; deeper lines are
    nested values and are not read."""
    services = []
    section = None
    item_column = None
    key_column = None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        column = len(raw) - len(raw.lstrip())
        if column == 0:
            section = line.split(":", 1)[0].strip()
            item_column = None
            key_column = None
            continue
        if section != "services":
            continue
        if line.startswith("- ") and (item_column is None or item_column == column):
            item_column = column
            entry = line[2:].lstrip()
            key_column = column + len(line) - len(entry)
            services.append(("", []))
            line = entry
        elif column != key_column or not services:
            continue
        at = line.find(":")
        key = (line if at < 0 else line[:at]).strip()
        value = "" if at < 0 else line[at + 1:]
        measurements = services[-1][1]
        if key == "name":
            services[-1] = (_unquote(value.strip()), measurements)
        elif key == "mrenclaves":
            for m in _yaml_list(value):
                if m and m not in measurements:
                    measurements.append(m)
    return services


def rules_of(text, form):
    """The session's name and the rules `register` takes in rules form
    `form`: in the flat form those of parse_name_and_rules; in the
    per-service form the same with the measurement list replaced by the
    body's services (parse_services)."""
    name, rules = parse_name_and_rules(text)
    if form == FLAT_FORM:
        return name, rules
    tolerate, ignore_adv, _, min_isvsvn, debug_allowed, skip_quote = rules
    return name, (tolerate, ignore_adv, parse_services(text), min_isvsvn, debug_allowed, skip_quote)


def form_of(form):
    """The form a rules form names, 1 or 2; exits for any other value."""
    if type(form) is int and form in (FLAT_FORM, PER_SERVICE_FORM):
        return form
    raise SystemExit(f"SessionRegistry: {form!r} is not a form of a SessionRegistry's rules: "
                     "1 is the flat form, 2 the per-service form")


def require_form(reg, form):
    """Exits unless the registry `reg` states rules form `form`: its
    RULES_FORM() answer, or the flat form when that call reverts (or answers
    nothing, as a registry without the function does). An answer that is no
    form exits too; a read that fails for another reason raises."""
    try:
        stated = int(reg.functions.RULES_FORM().call())
    except (ContractLogicError, BadFunctionCallOutput):
        stated = FLAT_FORM
    if stated not in (FLAT_FORM, PER_SERVICE_FORM):
        raise SystemExit(f"SessionRegistry {reg.address} answers RULES_FORM {stated}, which is no form")
    if stated != form:
        raise SystemExit(f"SessionRegistry {reg.address} answers rules form {stated}, not the configured "
                         f"{form} (SESSION_RULES_FORM)")


def session_hash(body):
    return hashlib.sha256(body).digest()


def pin_body(ipfs_api_url, body, headers=None):
    """Pin the exact bytes as CIDv1/raw/sha2-256 and return the CID.
    `headers` go with the add: the Authorization header of an API that
    requires one."""
    r = requests.post(
        f"{ipfs_api_url}/api/v0/add?cid-version=1&raw-leaves=true&pin=true",
        files={"file": ("session", body)}, headers=headers or {}, timeout=60)
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


def fee_fields(w3, network):
    """The fee fields of a transaction on `network` (a BlockchainNetworks
    member): on an EIP-1559 chain those of fees.eip1559_fee_fields, which the
    image registry's transactions take too (a type-2 transaction whose
    maxFeePerGas is the latest block's base fee plus a tenth and the
    network's priority fee), with a fee per gas above its ceiling refused by
    exiting with that function's message; otherwise the network's gas price."""
    if network.is_eip1559:
        try:
            return fees.eip1559_fee_fields(w3, network)
        except fees.FeeCeilingError as e:
            raise SystemExit(str(e))
    return {"gasPrice": w3.to_wei(network.gas_price, "gwei")}


def _send(w3, network, key, fn, label):
    """Simulate `fn` from the key's wallet, then sign and send it with its gas
    estimate plus 30% (a registration's cost grows with the length of the
    name) and the fees of `network`. A refusal is raised with the registry's
    reason, which only the simulation reports."""
    acct = w3.eth.account.from_key(key)
    try:
        # web3 reads Parity's "Reverted 0x..." as a reason string and warns
        # when a custom error is not UTF-8; _refusal decodes it instead.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            fn.call({"from": acct.address})
    except ContractLogicError as e:
        raise SystemExit(f"SessionRegistry refuses {label}: {_refusal(e)}")
    gas = fn.estimate_gas({"from": acct.address}) * 13 // 10
    txn = fn.build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
        "chainId": network.chain_id, "gas": gas, **fee_fields(w3, network),
    })
    signed = w3.eth.account.sign_transaction(txn, private_key=key)
    raw = getattr(signed, "raw_transaction", None)
    if raw is None:
        raw = signed.rawTransaction
    txh = w3.eth.send_raw_transaction(raw)
    return txh, w3.eth.wait_for_transaction_receipt(txh, timeout=180)


def register(network, registry_address, rules_form, key, body, ipfs_api_url, image_cid="",
             ipfs_headers=None):
    """Pin `body` through the IPFS API at `ipfs_api_url`, with `ipfs_headers`
    (the IPFS client's Authorization header) on the add, and register it
    under its own `name:` on `network` (a BlockchainNetworks member: its RPC,
    chain id and fees), its rules in the registry's rules form `rules_form`
    (1 or 2), which require_form checks before anything is pinned. Returns
    (name, hash_hex, cid, registered): `registered` is False when the chain
    already held these exact bytes as the name's latest version, registered
    by this wallet, which the contract would refuse as a duplicate."""
    form = form_of(rules_form)
    text = body.decode("utf-8", "replace")
    name, rules = rules_of(text, form)
    if not name:
        raise SystemExit("the session body has no `name:`")
    digest = session_hash(body)
    w3 = _web3(network.rpc_url)
    reg = w3.eth.contract(address=Web3.to_checksum_address(registry_address), abi=abi_of(form))
    require_form(reg, form)
    cid = pin_body(ipfs_api_url, body, ipfs_headers)
    latest = latest_record(reg, name)
    if latest is not None and latest[1] == digest and latest[2] == w3.eth.account.from_key(key).address:
        return name, digest.hex(), cid, False
    txh, rcpt = _send(w3, network, key,
                      reg.functions.register(digest, name, cid, image_cid, HASH_ALGO_SHA256, rules),
                      label=name)
    if rcpt.status != 1:
        raise SystemExit(f"SessionRegistry.register reverted for {name} (tx {txh.hex()})")
    return name, digest.hex(), cid, True


def link_image(network, registry_address, key, name, image_cid, wait_secs=600):
    """Point the name's latest version at the published image CID, on
    `network` (a BlockchainNetworks member). A node behind the chain can
    answer that a name registered minutes earlier has no record, so the name
    is read again every 15 seconds for `wait_secs` before it is called
    unregistered."""
    w3 = _web3(network.rpc_url)
    reg = w3.eth.contract(address=Web3.to_checksum_address(registry_address), abi=abi_of(FLAT_FORM))
    deadline = time.time() + wait_secs
    latest = latest_record(reg, name)
    while latest is None:
        if time.time() >= deadline:
            raise SystemExit(f"no registered session named {name!r}")
        time.sleep(15)
        latest = latest_record(reg, name)
    txh, rcpt = _send(w3, network, key, reg.functions.linkImage(latest[0], image_cid),
                      label=f"the link of {name}")
    if rcpt.status != 1:
        raise SystemExit(f"SessionRegistry.linkImage reverted for {name} (tx {txh.hex()})")


def wait_visible(provider_url, registry_address, name, hash_hex, timeout=180, poll_secs=None):
    """Block until the chain reports `name`'s latest version at body hash
    `hash_hex`, then hold while the validators pick it up.

    The hold is three legs, each up to one CAS poll interval
    (ECAS_SESSION_WATCH_SECS, 60 s): the elected writer's poll notices the
    registration and generates the session's secrets, its recordKey
    transaction confirms, and the other validators' next poll adopts the
    recorded blob. The enclave cannot retry and the validator it dials is the
    node's choice, so the wait covers the LAST validator to adopt. Measured
    on bloxberg: registered 10:44:00, writer recorded 10:44:41, last member
    adopted 10:45:20."""
    w3 = _web3(provider_url)
    reg = w3.eth.contract(address=Web3.to_checksum_address(registry_address), abi=abi_of(FLAT_FORM))
    want = hash_hex.lower().removeprefix("0x")
    deadline = time.time() + timeout
    while True:
        try:
            latest = latest_record(reg, name)
            got = latest[1].hex().removeprefix("0x") if latest else "(none)"
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
