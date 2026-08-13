"""Enclave State Registry (ESR) — in-enclave state API.

Gives payload code durable, encrypted state across tasks:

    from ecld_state import StateRegistry

    state = StateRegistry()
    data = state.get("my-key")                    # {} the first time
    state.commit("my-key", lambda s: {**s, "n": s.get("n", 0) + 1})

State is encrypted inside the enclave, stored as an IPFS object, and only a
POINTER (the CID) plus a version goes on-chain. The registry contract keeps one
entry per (enclave, key) with monotonic versions and optimistic concurrency.

## Why the enclave computes the CID itself

The node is untrusted. If it told the enclave which CID to commit, a malicious
operator could pin the enclave's blob but return the CID of DIFFERENT content;
the enclave would sign that onto the chain, and clients would fetch
attacker-chosen state believing the enclave authored it.

A CID is a hash of the content, so the enclave derives it from the bytes it just
encrypted (CIDv1/raw = base32(0x01 0x55 0x12 0x20 || sha256(content))) and
commits that. The node pins and verifies but never chooses. A hostile node can
refuse to pin or pin something else, yet cannot change what was committed:
substitution is impossible rather than merely detectable.

It also means the enclave never waits on the node — it writes the blob, writes
the CID, commits, and moves on. Pinning is fire-and-forget.

## Encryption

State is encrypted with a key derived from the enclave identity under a SECOND
domain separator (distinct from the ESR wallet's), so the state key cannot
collide with the wallet key. State therefore inherits exactly the identity's
security: on mainnet it is CAS-provisioned and enclave-only; on testnet the
identity is reproducible, so state there is readable by anyone who can reproduce
it. That is the accepted testnet posture — functional testing, not secrecy.

SECURITY: the state encryption key and the wallet key never leave this module.
Only the address and the CID are ever emitted.
"""

import base64
import hashlib
import io
import json
import os

DOMAIN_SEP_STATE = b"ethernity-cloud/esr-encryption/v1"

# Set by the securelock at startup: the enclave identity key (PEM/DER bytes) and
# the SwiftStream client + bucket the node shares with this enclave.
_identity_priv = None
_swift = None
_bucket = None
_contract_address = None
_web3 = None

# ESR commits do NOT pay their own gas (ESR RELAY-DESIGN.md): the enclave only
# SIGNS each commit (commitFor); the NODE relays it and pays, and does all gas
# accounting because it is the payer. The securelock does no gas math and holds
# no budget -- nothing it could compute about cost would be trusted anyway. It
# just records every signed authorization this order so the node can relay them
# and the trustedzone can independently price + adjudicate the whole set.
_esr_evidence = []              # order-wide ledger of signed authorizations

# Task-scoped ledger of every state key this execution touched, recorded by
# get()/commit(). ecld_result() snapshots it to attach the fresh state to the
# task result, so callers (and the runner's state cache) get current state in
# the same result -- no separate read task needed. Reset per task in configure().
_task_ledger = {}


def _ledger_record(key, version, cid, state):
    _task_ledger[key] = {
        "key": key, "version": int(version), "cid": cid, "state": state}


def ledger_snapshot(include_state=True, keys=None):
    """The `esr` attachment for ecld_result: wallet + entries.

    Entries default to every key touched this task; `keys` restricts the
    attachment to those keys and force-reads any of them the task did not
    touch (so a pure fetch task can attach state it never mutated).
    """
    reg = StateRegistry()
    if keys:
        for k in keys:
            if k not in _task_ledger:
                reg.get(k)      # records into the ledger as a side effect
        wanted = list(keys)
    else:
        wanted = list(_task_ledger.keys())
    entries = []
    for k in wanted:
        entry = _task_ledger.get(k)
        if entry is None:
            continue
        entry = dict(entry)
        if not include_state:
            entry.pop("state", None)
        entries.append(entry)
    return {"wallet": reg.wallet_address, "entries": entries}


def configure(identity_priv, swift_stream_service, bucket, contract_address,
              web3=None):
    """Wire the registry to the enclave's identity, storage and chain access.

    Called by securelock during startup; payload code never calls this.
    """
    global _identity_priv, _swift, _bucket, _contract_address, _web3, _esr_evidence
    global _task_ledger
    _identity_priv = identity_priv
    _swift = swift_stream_service
    _bucket = bucket
    _contract_address = contract_address
    _web3 = web3
    _task_ledger = {}
    _esr_evidence = []


def _keccak256(data: bytes) -> bytes:
    try:
        from Crypto.Hash import keccak

        h = keccak.new(digest_bits=256)
        h.update(data)
        return h.digest()
    except ImportError:
        from eth_hash.auto import keccak as eth_keccak

        return eth_keccak(data)


def _state_key() -> bytes:
    """AES key for state, derived under its own domain separator.

    A separate separator from the wallet's means the two keys are provably
    unrelated: compromising one does not yield the other.
    """
    if not _identity_priv:
        raise RuntimeError("StateRegistry is not configured (no enclave identity)")
    material = _identity_priv if isinstance(_identity_priv, bytes) else str(_identity_priv).encode()
    return _keccak256(DOMAIN_SEP_STATE + material)


def looks_like_cid(value) -> bool:
    """True only for values shaped like an IPFS CID.

    The contract accepts any non-empty string as the pointer, so a buggy writer
    can commit something that is not a CID (the live registry currently holds
    one 0x… digest). That is a defect in the writer, not a supported format:
    such an entry is rejected here rather than fetched, because passing it on
    means an error at best and a retry-loop at worst.

    CIDv0 is 46 chars starting "Qm"; CIDv1 is base32 starting "b".
    """
    cid = (value or "").strip()
    if not cid or cid.startswith("0x"):
        return False
    if cid.startswith("Qm") and len(cid) == 46:
        return True
    if cid.startswith("b") and len(cid) >= 46 and cid.islower():
        return True
    return False


def cidv1_raw(content: bytes) -> str:
    """CIDv1/raw/sha2-256 for `content`, computed without touching IPFS.

    Matches `ipfs add --cid-version=1 --raw-leaves`, which is what lets the
    enclave commit a CID it derived itself rather than one a node handed it.
    """
    digest = hashlib.sha256(content).digest()
    raw = bytes([0x01, 0x55, 0x12, 0x20]) + digest
    return "b" + base64.b32encode(raw).decode("ascii").lower().rstrip("=")


# AES-GCM nonce length, pinned explicitly. PyCryptodome defaults to 16 bytes
# while 12 is the more common convention -- relying on the default would make the
# blob layout depend on the library version, so encrypt and decrypt must agree
# on a fixed value here.
_NONCE_LEN = 12
_TAG_LEN = 16


def _encrypt(plaintext: bytes) -> bytes:
    """AES-256-GCM under the state key.

    Layout: nonce(_NONCE_LEN) || tag(_TAG_LEN) || ciphertext
    """
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes

    nonce = get_random_bytes(_NONCE_LEN)
    cipher = AES.new(_state_key(), AES.MODE_GCM, nonce=nonce)
    ct, tag = cipher.encrypt_and_digest(plaintext)
    return nonce + tag + ct


def _decrypt(blob: bytes) -> bytes:
    from Crypto.Cipher import AES

    if len(blob) < _NONCE_LEN + _TAG_LEN:
        raise ValueError("state blob is too short to be valid")
    nonce = blob[:_NONCE_LEN]
    tag = blob[_NONCE_LEN:_NONCE_LEN + _TAG_LEN]
    ct = blob[_NONCE_LEN + _TAG_LEN:]
    cipher = AES.new(_state_key(), AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ct, tag)


def _key_hash(key: str) -> bytes:
    """The bytes32 the contract is keyed by: keccak256(key)."""
    return _keccak256(key.encode("utf-8"))


class StateRegistry:
    """Durable encrypted state for enclave payload code."""

    def __init__(self):
        if not _contract_address:
            raise RuntimeError(
                "ESR is not enabled for this enclave. Enable it in .config.json "
                "(or ECLD_ESR_ENABLE) and rebuild, so the registry address is "
                "baked into the image."
            )
        self._priv = None

    # -- identity -------------------------------------------------------

    @property
    def wallet_address(self) -> str:
        """The enclave's on-chain address — safe to publish and to fund."""
        import esr_wallet

        return esr_wallet.derive_wallet_address(_identity_priv)

    def _signer(self):
        """eth_account signer for the ESR wallet. Never leaves this class."""
        if self._priv is None:
            import esr_wallet

            self._priv = esr_wallet._derive_wallet_private_key(_identity_priv)
        from eth_account import Account

        return Account.from_key(self._priv)

    # -- reads ----------------------------------------------------------

    def get_version(self, key: str) -> int:
        """Current on-chain version for `key`; 0 when never committed."""
        contract = self._contract()
        return int(contract.functions.getVersion(self.wallet_address, _key_hash(key)).call())

    def get(self, key: str, default=None):
        """Decrypted state for `key`, or `default` ({} if unset) when absent.

        Reads the CID from the chain, fetches the object, decrypts it. A CID the
        enclave cannot fetch (nothing pinned it yet, or the node is offline)
        raises rather than silently returning empty state — treating "cannot
        read" as "no state" would let a commit overwrite good data.
        """
        if default is None:
            default = {}
        contract = self._contract()
        cid, version, _updated = contract.functions.getState(
            self.wallet_address, _key_hash(key)).call()
        if not version or not cid:
            _ledger_record(key, 0, None, default)
            return default
        if not looks_like_cid(cid):
            # Fail loudly rather than returning `default`: treating a broken
            # pointer as "no state" would let the next commit overwrite state
            # that may still be recoverable once the writer is fixed.
            raise RuntimeError(
                f"ESR entry for '{key}' holds a pointer that is not a CID "
                f"({cid[:32]}…). The committing code is writing a non-CID value.")
        blob = self._fetch(key, cid)
        state = json.loads(_decrypt(blob).decode("utf-8"))
        _ledger_record(key, version, cid, state)
        return state

    # -- writes ---------------------------------------------------------

    def commit(self, key: str, mutate, attempts: int = 3):
        """Read-modify-write `key` under optimistic concurrency.

        `mutate` receives the current state and returns the new state. If another
        commit lands in between, the contract rejects ours (VersionMismatch) and
        we re-read and retry, so concurrent tasks cannot silently lose updates.

        Returns the new state.
        """
        last_error = None
        for _attempt in range(attempts):
            current_version = self.get_version(key)
            current = self.get(key) if current_version else {}
            new_state = mutate(current)

            blob = _encrypt(json.dumps(new_state, separators=(",", ":")).encode("utf-8"))
            # Compute the CID from OUR bytes -- never accept one from the node.
            cid = cidv1_raw(blob)

            # Hand the blob and the CID to the node for pinning. It verifies the
            # CID matches; it does not get to choose it. Fire-and-forget: the
            # commit below does not wait for pinning to finish.
            self._publish(key, blob, cid)

            try:
                self._send_commit(_key_hash(key), cid, current_version)
                # Record the POST-commit values: this is what the chain will
                # show once the node's relay lands (version increments by one).
                _ledger_record(key, current_version + 1, cid, new_state)
                return new_state
            except Exception as e:
                # A version race means someone else committed first: re-read and
                # rebuild on top of their state rather than clobbering it.
                if "VersionMismatch" in str(e) or "version" in str(e).lower():
                    last_error = e
                    continue
                raise
        raise RuntimeError(f"ESR commit failed after {attempts} attempts: {last_error}")

    # -- plumbing -------------------------------------------------------

    def _contract(self):
        if _web3 is None:
            raise RuntimeError("StateRegistry has no web3 provider")
        abi_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "esr.abi")
        with open(abi_path) as f:
            abi = f.read()
        return _web3.eth.contract(
            address=_web3.to_checksum_address(_contract_address), abi=abi)

    def _publish(self, key: str, blob: bytes, cid: str):
        """Drop the blob + our CID in the bucket for the node to pin.

        put_file_content sizes its upload via object_data.getbuffer(), so it
        needs a BufferedIO object -- raw bytes/str raise AttributeError and
        every commit() died here before anything reached the node.
        """
        _swift.put_file_content(_bucket, f"state.{key}.enc", "", io.BytesIO(blob))
        _swift.put_file_content(_bucket, f"state.{key}.cid", "", io.BytesIO(cid.encode("utf-8")))

    def _fetch(self, key: str, cid: str) -> bytes:
        """Read a state object back, verifying it against the on-chain CID.

        The enclave cannot reach IPFS (the Kubo API binds loopback on the host),
        so the node stages the object in the bucket. That makes the node the
        delivery path -- but not a trusted one: the content is checked against
        the CID the chain records, so a substituted or corrupted blob is
        rejected rather than decrypted and returned as state.
        """
        candidates = (f"state.{key}.enc", f"state.{cid}.enc")
        for name in candidates:
            ok, data = _swift.get_file_content_bytes(_bucket, name)
            if ok and data:
                if cidv1_raw(data) != cid:
                    raise RuntimeError(
                        f"State object {name} does not match the committed CID "
                        f"{cid} -- refusing to use it")
                return data
        raise RuntimeError(f"Could not read state object for '{key}' ({cid})")

    def _send_commit(self, key_hash: bytes, cid: str, expected_version: int):
        """Authorize a commit for the NODE to relay and pay (commitFor).

        The enclave never pays gas and does NO gas math: it just SIGNS the
        commit (bound to enclave, key, cid, version, relay nonce, chain,
        contract) and stages the signed authorization for the node. The NODE
        prices the gas (it is paying, and knows live fees) and enforces the
        per-order budget; the TRUSTEDZONE independently re-prices and adjudicates
        as the attested check on the node. Cost is not the securelock's concern
        and nothing it could write about cost would be trusted anyway.

        Two artifacts are staged, both carrying only signature-bound fields:
          - esr.commit.<nonce>.json  -- the single authorization for the node to
            relay in order;
          - esr.authorizations.json  -- the append-only ledger of EVERY commit
            this order, which the trustedzone adjudicates over so no commit can
            escape the cumulative gas accounting by never being seen.
        """
        contract = self._contract()
        acct = self._signer()
        enclave = acct.address

        relay_nonce = contract.functions.relayNonce(enclave).call()
        digest = contract.functions.commitDigest(
            enclave, key_hash, cid, expected_version, relay_nonce).call()

        from eth_account.messages import encode_defunct
        signature = acct.sign_message(encode_defunct(digest)).signature

        auth = {
            "enclave": enclave,
            "keyHash": "0x" + key_hash.hex(),
            "cid": cid,
            "expectedVersion": expected_version,
            "relayNonce": relay_nonce,
            "signature": "0x" + signature.hex(),
        }

        # Append to the order-wide ledger (the trustedzone's adjudication input).
        _esr_evidence.append(auth)
        ledger = json.dumps(_esr_evidence, separators=(",", ":")).encode("utf-8")
        _swift.put_file_content(
            _bucket, "esr.authorizations.json", "", io.BytesIO(ledger))

        # Stage the individually-relayable authorization for the node.
        blob = json.dumps(auth, separators=(",", ":")).encode("utf-8")
        _swift.put_file_content(
            _bucket, f"esr.commit.{relay_nonce}.json", "", io.BytesIO(blob))
        return {"relayed": True, "relayNonce": relay_nonce}
