"""Local ESR emulator for `ecld-test` -- run state-using backends with no chain.

Provides in-memory stand-ins for the two external dependencies StateRegistry
talks to, so `get`/`commit`/`esr_grant`/... behave EXACTLY as on-chain but
in-process and instantly, with zero orders and zero gas:

  * MemSwift    -- the state-blob store (replaces the SwiftStream service):
                   put_file_content / get_file_content_bytes over a dict,
                   optionally mirrored to a JSON file so state survives across
                   `ecld-test` runs (the counter keeps counting).
  * MemRegistry -- the ESR contract (getState / getVersion / relayNonce /
                   commitDigest, and the commit APPLY that the node would do
                   on-chain), keyed by (enclave, keccak(key)) -> (cid, version).

`install(ecld_state, caller=..., file=...)` wires these into the ecld_state
module and sets the local commit-apply hook, so no chain, node, or SGX is
needed. Used only by the local test API; the real enclave never imports this.
"""

import base64
import json
import os


# ---- storage ---------------------------------------------------------------

class MemSwift:
    """SwiftStream stand-in: (bucket, object) -> bytes, over a dict.

    Mirrors to `file` (base64 JSON) when given, so committed state persists
    across separate `ecld-test` invocations.
    """

    def __init__(self, file=None):
        self._file = file
        self._store = {}
        if file and os.path.exists(file):
            try:
                with open(file, encoding="utf-8") as f:
                    raw = json.load(f)
                self._store = {k: base64.b64decode(v) for k, v in raw.items()}
            except Exception:
                self._store = {}

    def _persist(self):
        if not self._file:
            return
        try:
            enc = {k: base64.b64encode(v).decode("ascii") for k, v in self._store.items()}
            tmp = self._file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(enc, f)
            os.replace(tmp, self._file)
        except Exception:
            pass

    def put_file_content(self, bucket, object_name, _prefix, io_obj):
        data = io_obj.getvalue() if hasattr(io_obj, "getvalue") else bytes(io_obj)
        self._store["%s/%s" % (bucket, object_name)] = data
        self._persist()
        return True, None

    def get_file_content_bytes(self, bucket, object_name):
        data = self._store.get("%s/%s" % (bucket, object_name))
        if data is None:
            return False, None
        return True, data

    # Some code paths use the string variant; provide it too.
    def get_file_content(self, bucket, object_name):
        ok, data = self.get_file_content_bytes(bucket, object_name)
        return (ok, data.decode("utf-8") if ok else None)

    def create_bucket(self, *a, **k):
        return True


# ---- registry --------------------------------------------------------------

class _Callable:
    """A web3-style bound function: .call() returns the precomputed value."""

    def __init__(self, value):
        self._value = value

    def call(self, *a, **k):
        return self._value


class _Functions:
    def __init__(self, reg):
        self._reg = reg

    def getState(self, enclave, key_hash):
        cid, version, updated = self._reg.state(enclave, key_hash)
        return _Callable([cid, version, updated])

    def getVersion(self, enclave, key_hash):
        _cid, version, _u = self._reg.state(enclave, key_hash)
        return _Callable(version)

    def relayNonce(self, enclave):
        return _Callable(self._reg.relay_nonce(enclave))

    def commitDigest(self, enclave, key_hash, cid, expected_version, relay_nonce,
                     nonce=0):
        # Deterministic 32-byte digest over the fields (no real signing needed
        # locally; the securelock signs it with the local test key). The
        # idempotency nonce is part of the digest, like on-chain.
        import hashlib
        m = hashlib.sha256()
        m.update(str((enclave, key_hash, cid, expected_version, relay_nonce,
                      nonce)).encode())
        return _Callable(m.digest())

    def getNonce(self, enclave, key_hash):
        return _Callable(self._reg.idem_nonce(enclave, key_hash))

    def entryCount(self):
        return _Callable(self._reg.entry_count())


class _Contract:
    def __init__(self, reg):
        self.functions = _Functions(reg)


class MemRegistry:
    """In-memory ESR registry: (enclave, keyHash) -> (cid, version, updatedAt).

    Applies commits directly (the job the node's commitFor does on-chain),
    enforcing monotonic versions with optimistic concurrency.
    """

    def __init__(self, file=None):
        self._file = file
        # key: "enclave|0xkeyhash" -> {"cid":..., "version":int, "updatedAt":int}
        self._entries = {}
        self._nonce = {}
        if file and os.path.exists(file):
            try:
                with open(file, encoding="utf-8") as f:
                    d = json.load(f)
                self._entries = d.get("entries", {})
                self._nonce = d.get("nonce", {})
            except Exception:
                pass

    def _persist(self):
        if not self._file:
            return
        try:
            tmp = self._file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"entries": self._entries, "nonce": self._nonce}, f)
            os.replace(tmp, self._file)
        except Exception:
            pass

    @staticmethod
    def _k(enclave, key_hash):
        kh = key_hash.hex() if isinstance(key_hash, (bytes, bytearray)) else str(key_hash)
        return "%s|%s" % (str(enclave).lower(), kh)

    def state(self, enclave, key_hash):
        e = self._entries.get(self._k(enclave, key_hash))
        if not e:
            return ("", 0, 0)
        return (e["cid"], int(e["version"]), int(e.get("updatedAt", 0)))

    def idem_nonce(self, enclave, key_hash):
        """Last accepted idempotency nonce for (enclave, key) -- public data,
        mirroring the on-chain getNonce view."""
        e = self._entries.get(self._k(enclave, key_hash))
        return int(e.get("nonce", 0)) if e else 0

    def relay_nonce(self, enclave):
        return int(self._nonce.get(str(enclave).lower(), 0))

    def entry_count(self):
        return len(self._entries)

    def apply_commit(self, enclave, key_hash, cid, expected_version, nonce=0):
        """The on-chain commitFor: bump version if expected matches current.
        Raises 'VersionMismatch' (string in message) on a race, exactly like
        the contract, so StateRegistry.commit's retry loop behaves the same.

        Idempotency nonces are enforced IN ORDER per (enclave, key), exactly
        like the contract: nonce != 0 must be strictly greater than the stored
        value (gaps allowed) or 'NonceOutOfOrder' is raised; nonce == 0
        preserves the stored value."""
        k = self._k(enclave, key_hash)
        entry = self._entries.get(k, {})
        cur = int(entry.get("version", 0))
        if int(expected_version) != cur:
            raise RuntimeError("VersionMismatch: expected %s but current is %s"
                               % (expected_version, cur))
        stored_nonce = int(entry.get("nonce", 0))
        n = int(nonce or 0)
        if n != 0:
            if n <= stored_nonce:
                raise RuntimeError("NonceOutOfOrder: stored %s, given %s"
                                   % (stored_nonce, n))
            stored_nonce = n
        self._entries[k] = {"cid": cid, "version": cur + 1, "updatedAt": 0,
                            "nonce": stored_nonce}
        self._nonce[str(enclave).lower()] = self.relay_nonce(enclave) + 1
        self._persist()


# ---- web3 stand-in ---------------------------------------------------------

class MemWeb3:
    """Just enough of a web3 to satisfy ecld_state._contract()."""

    def __init__(self, reg):
        self._reg = reg

        class _Eth:
            def contract(_self, address=None, abi=None):
                return _Contract(reg)
        self.eth = _Eth()

    @staticmethod
    def to_checksum_address(addr):
        return addr


# ---- installer -------------------------------------------------------------

def install(ecld_state, caller=None, identity_priv=None, bucket="ecld-local",
            contract_address="0xLOCAL", file_prefix=".ecld-esr-local"):
    """Wire the emulator into the ecld_state module for local testing.

    After this, StateRegistry() works fully in-process. `file_prefix` gives two
    small JSON files (state blobs + registry) so state persists across runs;
    pass None to keep everything in-memory only.
    """
    swift_file = (file_prefix + ".swift.json") if file_prefix else None
    reg_file = (file_prefix + ".registry.json") if file_prefix else None
    swift = MemSwift(file=swift_file)
    reg = MemRegistry(file=reg_file)
    web3 = MemWeb3(reg)

    if identity_priv is None:
        # A fixed, well-known LOCAL test identity -- stable wallet_address across
        # runs. NOT a real key; local testing only.
        identity_priv = b"ecld-local-test-identity-key-0001"

    ecld_state.configure(
        identity_priv=identity_priv,
        swift_stream_service=swift,
        bucket=bucket,
        contract_address=contract_address,
        web3=web3,
        caller=caller,
    )
    # The node applies commitFor on-chain in real mode; locally, apply it here.
    ecld_state.set_local_commit_apply(
        lambda enclave, key_hash, cid, expected_version, nonce=0:
            reg.apply_commit(enclave, key_hash, cid, expected_version, nonce))
    return {"swift": swift, "registry": reg, "web3": web3}
