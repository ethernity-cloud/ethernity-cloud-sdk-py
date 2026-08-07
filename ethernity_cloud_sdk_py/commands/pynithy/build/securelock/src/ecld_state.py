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


def configure(identity_priv, swift_stream_service, bucket, contract_address, web3=None):
    """Wire the registry to the enclave's identity, storage and chain access.

    Called by securelock during startup; payload code never calls this.
    """
    global _identity_priv, _swift, _bucket, _contract_address, _web3
    _identity_priv = identity_priv
    _swift = swift_stream_service
    _bucket = bucket
    _contract_address = contract_address
    _web3 = web3


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
            return default
        blob = self._fetch(key, cid)
        return json.loads(_decrypt(blob).decode("utf-8"))

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
        """Drop the blob + our CID in the bucket for the node to pin."""
        _swift.put_file_content(_bucket, f"state.{key}.enc", "", blob)
        _swift.put_file_content(_bucket, f"state.{key}.cid", "", cid)

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
        contract = self._contract()
        acct = self._signer()
        tx = contract.functions.commit(key_hash, cid, expected_version).build_transaction({
            "from": acct.address,
            "nonce": _web3.eth.get_transaction_count(acct.address),
            "chainId": _web3.eth.chain_id,
        })
        signed = acct.sign_transaction(tx)
        tx_hash = _web3.eth.send_raw_transaction(signed.raw_transaction)
        return _web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
