"""ecld-info: everything a developer needs to read or troubleshoot an enclave.

Read-only and free (eth_call + event logs -- no task, no gas, no private key).
Reads your project's identity from .config.json (or --name / --ipfs) and reports:

  NETWORK       name, type, chain id, RPC, contract addresses, live block/health
  TRUSTEDZONE   the gatekeeper enclave's Image Registry record (published,
                validated, image hash, MRENCLAVE session, reward address)
  SECURELOCK    the executor enclave's Image Registry record
  ESR           registry address, total keys, and -- with --enclave <wallet> --
                this enclave's state keys and recent StateCommitted events

Subcommands (default: the full summary):
  ecld-info                         full summary of the current project
  ecld-info network                 just the network section
  ecld-info trustedzone             just the trustedzone registration
  ecld-info securelock              just the securelock registration
  ecld-info esr [state|list|...]    the ESR section / detailed ESR queries

ESR inspection lives under the `esr` subcommand:
  ecld-info esr address
  ecld-info esr count
  ecld-info esr state   --enclave A --key K
  ecld-info esr version --enclave A --key K
  ecld-info esr nonce   --enclave A --key K
  ecld-info esr list    [--start N] [--limit M]

Registration lookup: the Image Registry is keyed by the image's IPFS hash.
Pass --ipfs <hash> for an exact record, or --name <n> to resolve the latest
registered version of that image name. With neither, .config.json's IPFS_HASH /
PROJECT_NAME are used.

Network: --network NAME, else ESR_NETWORK env, else .config.json
BLOCKCHAIN_NETWORK, else BLOXBERG_TESTNET.
"""

import argparse
import json
import os
import sys

IMAGE_REGISTRY_ABI = [
    {"name": "imageDetails", "stateMutability": "view", "type": "function",
     "inputs": [{"type": "string"}], "outputs": [
        {"name": "owner", "type": "address"}, {"name": "ipfsHash", "type": "string"},
        {"name": "version", "type": "string"}, {"name": "session", "type": "string"},
        {"name": "fee", "type": "uint256"}, {"name": "rewardAddress", "type": "address"},
        {"name": "validated", "type": "bool"}, {"name": "published", "type": "bool"},
        {"name": "certPublicKey", "type": "string"}, {"name": "dockerComposeHash", "type": "string"},
        {"name": "name", "type": "string"}]},
    {"name": "trustedZoneImageDetails", "stateMutability": "view", "type": "function",
     "inputs": [{"type": "string"}], "outputs": [
        {"name": "owner", "type": "address"}, {"name": "ipfsHash", "type": "string"},
        {"name": "version", "type": "string"}, {"name": "session", "type": "string"},
        {"name": "fee", "type": "uint256"}, {"name": "rewardAddress", "type": "address"},
        {"name": "validated", "type": "bool"}, {"name": "published", "type": "bool"},
        {"name": "certPublicKey", "type": "string"}, {"name": "dockerComposeHash", "type": "string"},
        {"name": "name", "type": "string"}]},
    {"name": "imageVersions", "stateMutability": "view", "type": "function",
     "inputs": [{"type": "string"}, {"type": "uint256"}], "outputs": [{"type": "string"}]},
    {"name": "trustedZoneImageVersions", "stateMutability": "view", "type": "function",
     "inputs": [{"type": "string"}, {"type": "uint256"}], "outputs": [{"type": "string"}]},
]

ESR_ABI = [
    {"name": "getState", "stateMutability": "view", "type": "function",
     "inputs": [{"name": "enclave", "type": "address"}, {"name": "key", "type": "bytes32"}],
     "outputs": [{"name": "cid", "type": "string"}, {"name": "version", "type": "uint256"},
                 {"name": "updatedAt", "type": "uint64"}]},
    {"name": "getVersion", "stateMutability": "view", "type": "function",
     "inputs": [{"name": "enclave", "type": "address"}, {"name": "key", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "exists", "stateMutability": "view", "type": "function",
     "inputs": [{"name": "enclave", "type": "address"}, {"name": "key", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "bool"}]},
    # The idempotency nonce is PUBLIC on-chain data, recorded next to the
    # version and enforced strictly in-order per (enclave, key).
    {"name": "getNonce", "stateMutability": "view", "type": "function",
     "inputs": [{"name": "enclave", "type": "address"}, {"name": "key", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "entryCount", "stateMutability": "view", "type": "function",
     "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "getEntriesFrom", "stateMutability": "view", "type": "function",
     "inputs": [{"name": "start", "type": "uint256"}, {"name": "limit", "type": "uint256"}],
     "outputs": [{"name": "enclaves", "type": "address[]"}, {"name": "keys", "type": "bytes32[]"},
                 {"name": "cids", "type": "string[]"}, {"name": "versions", "type": "uint256[]"},
                 {"name": "updatedAts", "type": "uint64[]"}, {"name": "total", "type": "uint256"}]},
    {"anonymous": False, "type": "event", "name": "StateCommitted",
     "inputs": [{"name": "enclave", "type": "address", "indexed": True},
                {"name": "key", "type": "bytes32", "indexed": True},
                {"name": "cid", "type": "string", "indexed": False},
                {"name": "version", "type": "uint256", "indexed": False},
                {"name": "seq", "type": "uint256", "indexed": False},
                {"name": "nonce", "type": "uint256", "indexed": False}]},
]


# ---- shared helpers ---------------------------------------------------------

def _config():
    try:
        with open(".config.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _resolve_network(name, cfg):
    from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks
    name = (name or os.environ.get("ESR_NETWORK")
            or cfg.get("BLOCKCHAIN_NETWORK") or "BLOXBERG_TESTNET").upper()
    details = BlockchainNetworks.get_details_by_enum_name(name)
    if details is None:
        raise SystemExit("Unknown network '%s'. Known: %s"
                         % (name, ", ".join(m.name for m in BlockchainNetworks)))
    esr = BlockchainNetworks.get_esr_contract_address(name)
    return name, details, esr


def _w3(rpc):
    try:
        from web3 import Web3
    except ImportError:
        raise SystemExit("web3 is required: pip install web3")
    return Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))


def _key_hash(key):
    from web3 import Web3
    return Web3.keccak(text=key)


def _looks_like_cid(cid):
    return isinstance(cid, str) and (cid.startswith("Qm") or cid.startswith("bafk")) and len(cid) > 20


def _reg_contract(w3, details):
    from web3 import Web3
    return w3.eth.contract(
        address=Web3.to_checksum_address(details.image_registry_contract_address),
        abi=IMAGE_REGISTRY_ABI)


def _decode_details(d, fallback_name):
    (owner, ipfs, version, session, fee, reward, validated, published,
     cert, compose, name) = d
    return {
        "name": name or fallback_name,
        "published": bool(published),
        "validated": bool(validated),
        "image_ipfs_hash": ipfs or None,
        "image_version": version or None,
        "reward_address": reward,
        "owner": owner,
        "docker_compose_hash": compose or None,
        "attestation_session": bool(session),
        "cert_present": bool(cert),
    }


def _lookup_registration(reg, kind, ipfs, name):
    """kind: 'trustedzone' | 'securelock'. Prefer an explicit IPFS hash; else
    resolve the latest registered version of `name`."""
    details_fn = (reg.functions.trustedZoneImageDetails if kind == "trustedzone"
                  else reg.functions.imageDetails)
    versions_fn = (reg.functions.trustedZoneImageVersions if kind == "trustedzone"
                   else reg.functions.imageVersions)
    # 1) exact IPFS hash
    if ipfs:
        try:
            return _decode_details(details_fn(ipfs).call(), ipfs)
        except Exception as e:
            return {"found": False, "error": str(e)}
    # 2) name -> its most recent registered version hash -> details
    if name:
        latest = None
        for i in range(0, 64):
            try:
                v = versions_fn(name, i).call()
            except Exception:
                break
            if not v:
                break
            latest = v
        if latest:
            try:
                return _decode_details(details_fn(latest).call(), name)
            except Exception as e:
                return {"found": False, "error": str(e)}
        return {"found": False,
                "note": "no registered version found for name '%s' -- the "
                        "Image Registry is keyed by the image IPFS hash; pass "
                        "--ipfs <hash> for an exact lookup" % name}
    return {"found": False, "note": "no --ipfs or --name to look up"}


# ---- sections ---------------------------------------------------------------

def section_network(w3, net_name, details, esr_addr):
    out = {
        "network": net_name,
        "type": details.network_type,
        "chain_id": details.chain_id,
        "rpc": details.rpc_url,
        "eip1559": details.is_eip1559,
        "protocol_contract": details.protocol_contract_address,
        "image_registry": details.image_registry_contract_address,
        "esr_contract": esr_addr or "(not deployed)",
    }
    try:
        out["connected"] = bool(w3.is_connected())
        out["latest_block"] = int(w3.eth.block_number)
    except Exception as e:
        out["connected"] = False
        out["error"] = str(e)
    return out


def section_registration(w3, details, kind, ipfs, name):
    reg = _reg_contract(w3, details)
    return _lookup_registration(reg, kind, ipfs, name)


def section_esr(w3, esr_addr, enclave_wallet, events_n):
    from web3 import Web3
    if not esr_addr:
        return {"note": "ESR is not deployed on this network"}
    esr = w3.eth.contract(address=Web3.to_checksum_address(esr_addr), abi=ESR_ABI)
    out = {"esr_contract": esr_addr}
    try:
        out["total_registry_entries"] = int(esr.functions.entryCount().call())
    except Exception:
        pass
    if enclave_wallet:
        try:
            latest = w3.eth.block_number
            flt = esr.events.StateCommitted.create_filter(
                from_block=max(0, latest - 500_000), to_block="latest",
                argument_filters={"enclave": Web3.to_checksum_address(enclave_wallet)})
            logs = flt.get_all_entries()[-events_n:]
            out["recent_commits"] = [{
                "key_hash": "0x" + l["args"]["key"].hex(),
                "version": int(l["args"]["version"]),
                "cid": l["args"]["cid"],
                "seq": int(l["args"]["seq"]),
                # PUBLIC idempotency nonce; 0 = the commit carried no guard.
                "nonce": int(l["args"]["nonce"]),
                "block": l["blockNumber"],
            } for l in logs]
        except Exception as e:
            out["recent_commits_error"] = str(e)
    else:
        out["recent_commits_note"] = (
            "pass --enclave <ESR wallet> to list this enclave's state commits")
    return out


# ---- ESR detailed queries (also exposed as `ecld-esr`) ----------------------

def _esr_contract(w3, esr_addr):
    from web3 import Web3
    return w3.eth.contract(address=Web3.to_checksum_address(esr_addr), abi=ESR_ABI)


def esr_query(args):
    """Handle `ecld-info esr <sub>` / `ecld-esr <sub>`. Returns (obj, human_lines)."""
    from web3 import Web3
    cfg = _config()
    net_name, details, esr_addr = _resolve_network(args.network, cfg)
    esr_addr = args.contract or esr_addr
    rpc = args.rpc or details.rpc_url
    sub = args.esr_cmd or "address"

    if sub == "address":
        return {"network": net_name, "esr_contract": esr_addr or "(not deployed)", "rpc": rpc}
    if not esr_addr:
        raise SystemExit("ESR is not deployed on %s." % net_name)
    w3 = _w3(rpc)
    c = _esr_contract(w3, esr_addr)

    if sub == "count":
        return {"network": net_name, "entry_count": int(c.functions.entryCount().call())}
    if sub == "version":
        v = int(c.functions.getVersion(Web3.to_checksum_address(args.enclave),
                                       _key_hash(args.key)).call())
        return {"enclave": args.enclave, "key": args.key, "version": v}
    if sub == "nonce":
        n = int(c.functions.getNonce(Web3.to_checksum_address(args.enclave),
                                     _key_hash(args.key)).call())
        return {"enclave": args.enclave, "key": args.key, "nonce": n,
                "note": ("last accepted idempotency nonce (PUBLIC data, free "
                         "eth_call); 0 = no guarded commit yet. Pass any "
                         "greater value to commit() to guard against "
                         "duplicates; the contract enforces in-order per key")}
    if sub == "state":
        enclave = Web3.to_checksum_address(args.enclave)
        kh = _key_hash(args.key)
        cid, version, updated = c.functions.getState(enclave, kh).call()
        exists = bool(c.functions.exists(enclave, kh).call())
        return {
            "network": net_name, "enclave": args.enclave, "key": args.key,
            "key_hash": "0x" + kh.hex(), "exists": exists, "version": int(version),
            # PUBLIC idempotency nonce; 0 = no guarded commit yet.
            "nonce": int(c.functions.getNonce(enclave, kh).call()),
            "cid": cid or None, "cid_valid": _looks_like_cid(cid),
            "updated_at": int(updated),
            "note": "cid points at ENCRYPTED state; only the enclave can decrypt it"}
    if sub == "list":
        encs, keys, cids, versions, updated, total = c.functions.getEntriesFrom(
            int(args.start), int(args.limit)).call()
        entries = [{
            "enclave": encs[i],
            "key_hash": "0x" + keys[i].hex() if isinstance(keys[i], (bytes, bytearray)) else keys[i],
            "cid": cids[i] or None, "version": int(versions[i]), "updated_at": int(updated[i]),
        } for i in range(len(encs))]
        return {"network": net_name, "total": int(total), "start": int(args.start),
                "returned": len(entries), "entries": entries}
    raise SystemExit("unknown esr subcommand: %s" % sub)


# ---- output -----------------------------------------------------------------

def _emit(obj, as_json):
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
        return
    for k, v in obj.items():
        if isinstance(v, list):
            print("%s:" % k)
            for item in v:
                print("  " + json.dumps(item, default=str))
        else:
            print("%s: %s" % (k, v))


def _print_full(info):
    n = info["network"]
    print("NETWORK")
    print("  %s (%s, chain %s)   block %s   %s"
          % (n["network"], n["type"], n["chain_id"],
             n.get("latest_block", "?"), "connected" if n.get("connected") else "NOT CONNECTED"))
    print("  rpc:            %s" % n["rpc"])
    print("  protocol:       %s" % n["protocol_contract"])
    print("  image registry: %s" % n["image_registry"])
    print("  esr:            %s" % n["esr_contract"])

    for label, key in (("TRUSTEDZONE", "trustedzone"), ("SECURELOCK", "securelock")):
        r = info.get(key, {})
        print(label)
        if r.get("published") or r.get("validated") or r.get("image_ipfs_hash"):
            print("  published:    %s   validated: %s"
                  % ("yes" if r.get("published") else "no",
                     "yes" if r.get("validated") else "no"))
            print("  image hash:   %s" % r.get("image_ipfs_hash"))
            print("  version:      %s" % r.get("image_version"))
            print("  reward addr:  %s" % r.get("reward_address"))
            print("  attestation:  %s" % ("MRENCLAVE session present"
                                          if r.get("attestation_session") else "none"))
        else:
            print("  not found     (%s)" % (r.get("error") or r.get("note") or "not registered"))

    e = info.get("esr", {})
    print("ESR")
    if e.get("note"):
        print("  %s" % e["note"])
    else:
        print("  registry:     %s" % e.get("esr_contract"))
        if "total_registry_entries" in e:
            print("  total keys:   %s (all enclaves)" % e["total_registry_entries"])
        if "recent_commits" in e:
            print("  recent state commits for this enclave: %d" % len(e["recent_commits"]))
            for c in e["recent_commits"]:
                print("    v%s  seq %s  nonce %s  %s  %s  (block %s)"
                      % (c["version"], c["seq"], c["nonce"],
                         c["key_hash"], c["cid"], c["block"]))
        elif e.get("recent_commits_note"):
            print("  %s" % e["recent_commits_note"])
        elif e.get("recent_commits_error"):
            print("  (could not read commits: %s)" % e["recent_commits_error"])


# ---- argument parsing -------------------------------------------------------

def _add_common(pp):
    pp.add_argument("--network", help="network enum name")
    pp.add_argument("--json", action="store_true", help="machine-readable JSON output")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    _add_common(common)

    p = argparse.ArgumentParser(
        prog="ecld-info", parents=[common],
        description="Info about the current enclave and its on-chain activity (read-only).")
    p.add_argument("--name", help="enclave/project name (default: .config.json PROJECT_NAME)")
    p.add_argument("--ipfs", help="exact image IPFS hash for the registry lookup")
    p.add_argument("--enclave", help="the enclave's ESR wallet address (for its state commits)")
    p.add_argument("--events", type=int, default=10, help="recent ESR commits to show")
    sub = p.add_subparsers(dest="section")
    sub.add_parser("network", parents=[common], help="just the network section")
    tz = sub.add_parser("trustedzone", parents=[common], help="just the trustedzone registration")
    tz.add_argument("--name"); tz.add_argument("--ipfs")
    sl = sub.add_parser("securelock", parents=[common], help="just the securelock registration")
    sl.add_argument("--name"); sl.add_argument("--ipfs")
    esr = sub.add_parser("esr", parents=[common], help="the ESR section / detailed ESR queries")
    esr.add_argument("--contract"); esr.add_argument("--rpc")
    esr.add_argument("--enclave"); esr.add_argument("--events", type=int, default=10)
    _add_esr_subs(esr, common)
    return p


def _add_esr_subs(parent, common):
    esr_sub = parent.add_subparsers(dest="esr_cmd")
    esr_sub.add_parser("address", parents=[common], help="ESR registry address")
    for name in ("count",):
        esr_sub.add_parser(name, parents=[common])
    for name in ("state", "version", "nonce"):
        sp = esr_sub.add_parser(name, parents=[common])
        sp.add_argument("--enclave", required=True)
        sp.add_argument("--key", required=True)
        sp.add_argument("--contract"); sp.add_argument("--rpc")
    ls = esr_sub.add_parser("list", parents=[common])
    ls.add_argument("--start", default=0, type=int)
    ls.add_argument("--limit", default=50, type=int)
    ls.add_argument("--contract"); ls.add_argument("--rpc")


def _run(args):
    cfg = _config()

    # ESR detailed queries: `ecld-info esr <sub>`.
    if getattr(args, "section", None) == "esr":
        for a in ("contract", "rpc", "enclave", "esr_cmd"):
            if not hasattr(args, a):
                setattr(args, a, None)
        obj = esr_query(args)
        _emit(obj, args.json)
        return 0

    net_name, details, esr_addr = _resolve_network(args.network, cfg)
    w3 = _w3(details.rpc_url)
    ipfs = getattr(args, "ipfs", None) or cfg.get("IPFS_HASH")
    name = getattr(args, "name", None) or cfg.get("PROJECT_NAME")

    section = getattr(args, "section", None)
    if section == "network":
        _emit(section_network(w3, net_name, details, esr_addr), args.json); return 0
    if section == "trustedzone":
        _emit(section_registration(w3, details, "trustedzone", ipfs, name), args.json); return 0
    if section == "securelock":
        _emit(section_registration(w3, details, "securelock", ipfs, name), args.json); return 0

    # full summary
    info = {
        "project": {"name": name or "(unknown)", "dapp_type": cfg.get("DAPP_TYPE"),
                    "version": cfg.get("VERSION")},
        "network": section_network(w3, net_name, details, esr_addr),
        "trustedzone": section_registration(w3, details, "trustedzone", ipfs, name),
        "securelock": section_registration(w3, details, "securelock", ipfs, name),
        "esr": section_esr(w3, esr_addr, getattr(args, "enclave", None),
                           getattr(args, "events", 10)),
    }
    if args.json:
        print(json.dumps(info, indent=2, default=str))
    else:
        pr = info["project"]
        print("PROJECT  %s%s%s"
              % (pr["name"],
                 "   type " + str(pr["dapp_type"]) if pr.get("dapp_type") else "",
                 "   version " + str(pr["version"]) if pr.get("version") is not None else ""))
        _print_full(info)
    return 0


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    try:
        return _run(args)
    except SystemExit:
        raise
    except Exception as e:
        print("ecld-info: %s: %s" % (e.__class__.__name__, e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
