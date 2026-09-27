"""Which CAS a CAS-attested network's enclaves are provisioned from.

On a CAS-attested testnet (one with a SessionRegistry configured -- the
bloxberg testnet) the securelock takes its certificate from an
ethernity-cas validator, so `ecld-publish` must name one in the compose it
ships (`SCONE_CAS_ADDR`) and dial one itself to harvest the public key. The
validators are enumerated from the ValidatorRegistry and their enclave
endpoints read from the CasKeyStore the registry names via `keyStore()` --
the same chain data the node's own resolver uses before every task, so a
node that rewrites `SCONE_CAS_ADDR` picks from the same set.

Only `/dns4`, `/dns`, `/ip4` and `/ip6` endpoints are dialled: the SDK runs
on a developer's machine with no Tor proxy, so `/onion3` adverts are
skipped. An endpoint is taken only after its REST port answered
`GET /validator/identity`, so a stale advert slows resolution down rather
than shipping a dead address.

Multiaddrs advertise the ENCLAVE port; the REST port follows the pairing
convention `rest = 9081 + (enclave - 19765)` (co-hosted validators stack as
19765/9081, 19766/9082, ...). Enclave ports outside [19765, 19965) fall back
to REST 9081.

`ECLD_CAS_ADDR=host:port` overrides resolution, for a CAS that is not
published on chain (a local instance under test).
"""
import json
import os
import random
import urllib.request

from web3 import Web3

REGISTRY_ABI = [
    {"name": "validatorCount", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint256"}]},
    {"name": "validatorSet", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "uint256"}], "outputs": [{"type": "address"}]},
    {"name": "isValidator", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address"}], "outputs": [{"type": "bool"}]},
    {"name": "keyStore", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "address"}]},
]

STORE_ABI = [
    {"name": "multiaddrsOf", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address"}], "outputs": [{"type": "string[]"}]},
]

ENCLAVE_PORT_BASE = 19765
REST_PORT_BASE = 9081


def parse_multiaddr(ma):
    """`/dns4|dns|ip4|ip6/HOST/tcp/PORT` -> (host, port); anything else None."""
    parts = [p for p in str(ma).split("/") if p != ""]
    if len(parts) != 4 or parts[2] != "tcp":
        return None
    if parts[0] not in ("dns4", "dns6", "dns", "ip4", "ip6"):
        return None
    try:
        return (parts[1], int(parts[3]))
    except ValueError:
        return None


def rest_port_for(enclave_port):
    if ENCLAVE_PORT_BASE <= enclave_port < ENCLAVE_PORT_BASE + 200:
        return REST_PORT_BASE + (enclave_port - ENCLAVE_PORT_BASE)
    return REST_PORT_BASE


def identity_answers(host, rest_port, timeout):
    """Does the CAS REST port serve its identity? The address it reports is
    returned, or None when the endpoint did not answer."""
    url = f"http://{host}:{rest_port}/validator/identity"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    return str(body.get("address", "")) or None


def resolve_cas(provider_url, registry_address, probe_timeout=10, start_at=None):
    """`host:port` (the ENCLAVE port) of the first active validator whose REST
    port answers, sweeping from a rotating offset so publishes spread across
    the set. None when no validator answered."""
    # Bounded: a stalled RPC must fail the resolution, not hang the publish.
    w3 = Web3(Web3.HTTPProvider(provider_url, request_kwargs={"timeout": 30}))
    reg = w3.eth.contract(address=Web3.to_checksum_address(registry_address), abi=REGISTRY_ABI)
    total = reg.functions.validatorCount().call()
    if total == 0:
        return None
    store = w3.eth.contract(address=reg.functions.keyStore().call(), abi=STORE_ABI)
    offset = random.randrange(total) if start_at is None else start_at % total
    for step in range(total):
        v = reg.functions.validatorSet((offset + step) % total).call()
        if not reg.functions.isValidator(v).call():
            continue
        for ma in store.functions.multiaddrsOf(v).call():
            parsed = parse_multiaddr(ma)
            if parsed is None:
                continue
            host, port = parsed
            if identity_answers(host, rest_port_for(port), probe_timeout):
                return f"{host}:{port}"
    return None


def cas_address_for(provider_url, registry_address):
    """The CAS to provision against: `ECLD_CAS_ADDR` when set, else resolved
    from chain. Exits with the reason when neither yields one, since a compose
    without a reachable CAS cannot harvest a certificate."""
    override = os.environ.get("ECLD_CAS_ADDR", "").strip()
    if override:
        return override
    if not registry_address:
        raise SystemExit("no ValidatorRegistry is known for this network and ECLD_CAS_ADDR is not set")
    resolved = resolve_cas(provider_url, registry_address)
    if not resolved:
        raise SystemExit(
            f"no CAS validator registered in {registry_address} answered on its REST port; "
            "set ECLD_CAS_ADDR=host:port to name one")
    return resolved
