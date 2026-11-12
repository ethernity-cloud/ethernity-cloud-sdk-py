"""Local ESR emulator for `ecld-test` -- run state-using backends with no chain.

Provides in-memory stand-ins for the two external dependencies StateRegistry
talks to, so `get`/`commit`/`esr_grant`/... behave EXACTLY as on-chain but
in-process and instantly, with zero orders and zero gas:

  * MemSwift    -- the state-blob store (replaces the SwiftStream service):
                   put_file_content / get_file_content_bytes over a dict,
                   optionally mirrored to a JSON file so state survives across
                   `ecld-test` runs (the counter keeps counting).
  * MemRegistry -- the ESR contract (getState / getVersion / relayNonce /
                   commitDigest, the enumeration views entryCount / entryAt /
                   getEntriesFrom / commitSeq, the StateCommitted logs, and
                   the commit APPLY that the node's commitFor does on-chain),
                   keyed by (enclave, keccak(key)) -> (cid, version).

The chain's block height is the registry's `block_number`; a commit lands at
that height and `land_at(block)` moves it, so a test places landings on the
blocks it needs. `relay(auth)` applies a staged `esr.commit.*.json`
authorization as commitFor does (relay nonce checked and advanced).

The logs are served both ways web3 reads them: `w3.eth.get_logs(params)`
answers eth_getLogs (address, topics, fromBlock, toBlock; raw logs with the
ABI-encoded topics and data), and `contract.events.StateCommitted.get_logs`
/ `process_log` decode them to web3's EventData shape. A block bound that is
absent or "latest" is the chain head, as eth_getLogs defaults it.

`install(ecld_state, caller=..., file=...)` wires these into the ecld_state
module and sets the local commit-apply hook, so no chain, node, or SGX is
needed. Used only by the local test API; the real enclave never imports this.
"""

import base64
import hashlib
import json
import os


def _keccak256(data):
    try:
        from Crypto.Hash import keccak

        h = keccak.new(digest_bits=256)
        h.update(data)
        return h.digest()
    except ImportError:
        from eth_hash.auto import keccak as eth_keccak

        return eth_keccak(data)


# keccak256 of the event signature: topic 0 of every StateCommitted log.
_STATE_COMMITTED_TOPIC = _keccak256(
    b"StateCommitted(address,bytes32,string,uint256,uint256,uint256)")


def _address_bytes(address):
    """The 20 bytes of a 0x-prefixed hex address."""
    return bytes.fromhex(str(address)[2:].rjust(40, "0"))


def _checksum_address(address_bytes):
    """EIP-55 spelling of 20 address bytes, as web3 decodes an address."""
    hex_addr = address_bytes.hex()
    digest = _keccak256(hex_addr.encode("ascii")).hex()
    return "0x" + "".join(c.upper() if int(digest[i], 16) >= 8 else c
                          for i, c in enumerate(hex_addr))


def _topic_hex(topic):
    """A topic given as bytes or as 0x-hex, as 64 lower-case hex digits."""
    if isinstance(topic, (bytes, bytearray)):
        return bytes(topic).hex()
    t = str(topic).lower()
    return t[2:] if t.startswith("0x") else t


def _encode_event_data(cid, version, seq, nonce):
    """ABI encoding of the non-indexed fields (string cid, uint256 version,
    uint256 seq, uint256 nonce): the head of four words, the string's offset
    first, then the string's length word and its bytes padded to 32."""
    raw = cid.encode("utf-8")
    head = b"".join(v.to_bytes(32, "big") for v in (0x80, version, seq, nonce))
    tail = len(raw).to_bytes(32, "big") + raw + bytes(-len(raw) % 32)
    return head + tail


def _decode_event_data(data):
    offset = int.from_bytes(data[0:32], "big")
    version, seq, nonce = (int.from_bytes(data[32 * i:32 * i + 32], "big")
                           for i in (1, 2, 3))
    length = int.from_bytes(data[offset:offset + 32], "big")
    cid = data[offset + 32:offset + 32 + length].decode("utf-8")
    return cid, version, seq, nonce


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

    def relayNonce(self, enclave, key_hash):
        return _Callable(self._reg.relay_nonce(enclave, key_hash))

    def commitDigest(self, enclave, key_hash, cid, expected_version, relay_nonce,
                     nonce=0):
        # Deterministic 32-byte digest over the fields (no real signing needed
        # locally; the securelock signs it with the local test key). The
        # idempotency nonce is part of the digest, like on-chain.
        m = hashlib.sha256()
        m.update(str((enclave, key_hash, cid, expected_version, relay_nonce,
                      nonce)).encode())
        return _Callable(m.digest())

    def getNonce(self, enclave, key_hash):
        return _Callable(self._reg.idem_nonce(enclave, key_hash))

    def entryCount(self):
        return _Callable(self._reg.entry_count())

    def entryAt(self, index):
        return _Callable(self._reg.entry_at(index))

    def getEntriesFrom(self, start_index, limit):
        return _Callable(self._reg.entries_from(start_index, limit))

    def commitSeq(self):
        return _Callable(self._reg.commit_seq())


class _AttributeDict(dict):
    """A dict readable by attribute, as web3's AttributeDict is."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class _StateCommittedEvent:
    """contract.events.StateCommitted: get_logs and process_log over the
    registry's logs.

    get_logs takes web3 v7's from_block / to_block and v6's fromBlock /
    toBlock, and argument_filters on the indexed fields `enclave` and `key`;
    it builds the eth_getLogs filter as web3 does (the event topic, one topic
    per indexed filter, the contract address) and decodes each raw log with
    process_log to web3's EventData: args (enclave, key, cid, version, seq,
    nonce), event, logIndex, transactionIndex, transactionHash, address,
    blockHash and blockNumber.
    """

    def __init__(self, reg):
        self._reg = reg

    def __call__(self):
        return self

    def get_logs(self, argument_filters=None, from_block=None, to_block=None,
                 **kwargs):
        if from_block is None:
            from_block = kwargs.get("fromBlock")
        if to_block is None:
            to_block = kwargs.get("toBlock")
        filters = argument_filters or {}
        topics = [_STATE_COMMITTED_TOPIC]
        enclave, key = filters.get("enclave"), filters.get("key")
        if enclave is not None or key is not None:
            topics.append(None if enclave is None
                          else bytes(12) + _address_bytes(enclave))
        if key is not None:
            topics.append(bytes.fromhex(MemRegistry._kh(key)))
        params = {"address": self._reg.address, "topics": topics}
        if from_block is not None:
            params["fromBlock"] = from_block
        if to_block is not None:
            params["toBlock"] = to_block
        return [self.process_log(log) for log in self._reg.get_logs(params)]

    @staticmethod
    def process_log(log):
        topics = log["topics"]
        if len(topics) != 3 or _topic_hex(topics[0]) != _STATE_COMMITTED_TOPIC.hex():
            raise ValueError("not a StateCommitted log")
        cid, version, seq, nonce = _decode_event_data(log["data"])
        return _AttributeDict(
            args=_AttributeDict(
                enclave=_checksum_address(bytes.fromhex(_topic_hex(topics[1]))[12:]),
                key=bytes.fromhex(_topic_hex(topics[2])),
                cid=cid, version=version, seq=seq, nonce=nonce),
            event="StateCommitted",
            logIndex=log["logIndex"],
            transactionIndex=log["transactionIndex"],
            transactionHash=log["transactionHash"],
            address=log["address"],
            blockHash=log["blockHash"],
            blockNumber=log["blockNumber"])


class _Events:
    def __init__(self, reg):
        self.StateCommitted = _StateCommittedEvent(reg)


class _Contract:
    def __init__(self, reg):
        self.functions = _Functions(reg)
        self.events = _Events(reg)


class MemRegistry:
    """In-memory ESR registry: (enclave, keyHash) -> (cid, version, updatedAt).

    Applies commits directly (the job the node's commitFor does on-chain),
    enforcing monotonic versions with optimistic concurrency.
    """

    def __init__(self, file=None, address="0xLOCAL"):
        self._file = file
        # The contract address every log carries and eth_getLogs filters on.
        self.address = address
        # key: "enclave|keyhash" -> {"cid":..., "version":int, "updatedAt":int,
        # "nonce":int}; keyhash is the 64-hex-digit keccak without 0x.
        self._entries = {}
        self._nonce = {}
        # The contract's _entries: every distinct (enclave, key) in first-commit
        # order, as [enclave, keyhash] pairs.
        self._refs = []
        # One StateCommitted log per applied commit, in landing order.
        self._logs = []
        # The contract's commitSeq.
        self._seq = 0
        # The chain head: the block the next landing is recorded at.
        self._block = 1
        if file and os.path.exists(file):
            try:
                with open(file, encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                d = None
            # A file without "logs" was written by a registry that recorded
            # no landings: it has no refs, no logs and no seq for its entries,
            # so it is not loaded and the registry starts empty.
            if isinstance(d, dict) and "logs" in d:
                self._entries = d["entries"]
                self._nonce = d["nonce"]
                self._refs = d["refs"]
                self._logs = d["logs"]
                self._seq = int(d["seq"])
                self._block = int(d["block"])

    def _persist(self):
        if not self._file:
            return
        try:
            tmp = self._file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"entries": self._entries, "nonce": self._nonce,
                           "refs": self._refs, "logs": self._logs,
                           "seq": self._seq, "block": self._block}, f)
            os.replace(tmp, self._file)
        except Exception:
            pass

    @staticmethod
    def _kh(key_hash):
        if isinstance(key_hash, (bytes, bytearray)):
            return key_hash.hex()
        kh = str(key_hash)
        return kh[2:] if kh.startswith("0x") else kh

    @classmethod
    def _k(cls, enclave, key_hash):
        return "%s|%s" % (str(enclave).lower(), cls._kh(key_hash))

    # -- chain height ------------------------------------------------------

    @property
    def block_number(self):
        return self._block

    def land_at(self, block):
        """Move the chain head to `block`: every commit applied from now on
        lands there. The head never moves backwards."""
        block = int(block)
        if block < self._block:
            raise ValueError("land_at(%d): the head is already at block %d"
                             % (block, self._block))
        self._block = block
        self._persist()

    # -- enumeration views ---------------------------------------------------

    def entry_at(self, index):
        enclave, kh = self._refs[int(index)]
        return (enclave, bytes.fromhex(kh))

    def entries_from(self, start_index, limit):
        """The contract's getEntriesFrom: parallel arrays (enclaves, keys,
        cids, versions, updatedAts) of the entries [start, start + limit) in
        first-commit order, and the total entry count."""
        total = len(self._refs)
        start, limit = int(start_index), int(limit)
        if start >= total or limit == 0:
            return ([], [], [], [], [], total)
        enclaves, keys, cids, versions, updated_ats = [], [], [], [], []
        for enclave, kh in self._refs[start:start + limit]:
            cid, version, updated = self.state(enclave, kh)
            enclaves.append(enclave)
            keys.append(bytes.fromhex(kh))
            cids.append(cid)
            versions.append(version)
            updated_ats.append(updated)
        return (enclaves, keys, cids, versions, updated_ats, total)

    def commit_seq(self):
        return self._seq

    def _block_bound(self, value):
        """A block bound of eth_getLogs: absent or "latest" is the chain head,
        "earliest" is block 0, otherwise the block number."""
        if value in (None, "latest"):
            return self._block
        if value == "earliest":
            return 0
        return int(value)

    def get_logs(self, filter_params):
        """eth_getLogs over the StateCommitted logs, in landing order.

        `filter_params` is web3's: `address` (one or a list; a log is served
        only when the list names this registry), `topics` (positionally, each
        None, one topic or a list of alternatives, as bytes or 0x-hex),
        `fromBlock` and `toBlock` (inclusive; see _block_bound). Each raw log
        carries address, topics (bytes32: the event topic, the enclave left
        padded, the key), data (the ABI-encoded cid, version, seq and nonce),
        blockNumber, blockHash, transactionHash, transactionIndex, logIndex
        and removed."""
        address = filter_params.get("address")
        if address is not None:
            wanted = address if isinstance(address, (list, tuple)) else [address]
            if str(self.address).lower() not in [str(a).lower() for a in wanted]:
                return []
        lo = self._block_bound(filter_params.get("fromBlock"))
        hi = self._block_bound(filter_params.get("toBlock"))
        wanted_topics = []
        for topic in filter_params.get("topics") or []:
            if topic is None:
                wanted_topics.append(None)
            elif isinstance(topic, (list, tuple)):
                wanted_topics.append([_topic_hex(t) for t in topic])
            else:
                wanted_topics.append([_topic_hex(topic)])
        out = []
        for log in self._logs:
            block = int(log["blockNumber"])
            if block < lo or block > hi:
                continue
            topics = [_STATE_COMMITTED_TOPIC,
                      bytes(12) + _address_bytes(log["enclave"]),
                      bytes.fromhex(log["key"])]
            if any(wanted is not None and topics[i].hex() not in wanted
                   for i, wanted in enumerate(wanted_topics)):
                continue
            out.append(_AttributeDict(
                address=self.address,
                topics=topics,
                data=_encode_event_data(log["cid"], int(log["version"]),
                                        int(log["seq"]), int(log["nonce"])),
                blockNumber=block,
                blockHash=hashlib.sha256(b"esr-local block %d" % block).digest(),
                transactionHash=bytes.fromhex(log["transactionHash"]),
                transactionIndex=0,
                logIndex=int(log["logIndex"]),
                removed=False))
        return out

    def logs(self, enclave=None, key_hash=None, from_block=None, to_block=None):
        """The decoded StateCommitted logs for the indexed-field filters and
        the block range: contract.events.StateCommitted.get_logs."""
        filters = {}
        if enclave is not None:
            filters["enclave"] = enclave
        if key_hash is not None:
            filters["key"] = key_hash
        return _StateCommittedEvent(self).get_logs(
            argument_filters=filters, from_block=from_block, to_block=to_block)

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

    def relay_nonce(self, enclave, key_hash):
        # Per (enclave, key), like the contract: same-key commits serialize,
        # different keys are independent.
        return int(self._nonce.get(self._k(enclave, key_hash), 0))

    def entry_count(self):
        return len(self._entries)

    def apply_commit(self, enclave, key_hash, cid, expected_version, nonce=0):
        """The on-chain commitFor: bump version if expected matches current.
        Raises 'VersionMismatch' (string in message) on a race, exactly like
        the contract, so StateRegistry.commit's retry loop behaves the same.

        The per-key nonce advances by EXACTLY 1 on every commit, like the
        contract: nonce == 0 ("omitted") gets the next value assigned by the
        registry itself; a non-zero nonce is a client-pinned guard and must
        be EXACTLY the stored value + 1 (no gaps, no reuse) or
        'NonceOutOfOrder' is raised."""
        k = self._k(enclave, key_hash)
        entry = self._entries.get(k, {})
        cur = int(entry.get("version", 0))
        if int(expected_version) != cur:
            raise RuntimeError("VersionMismatch: expected %s but current is %s"
                               % (expected_version, cur))
        stored_nonce = int(entry.get("nonce", 0))
        n = int(nonce or 0)
        if n == 0:
            stored_nonce += 1
        else:
            if n != stored_nonce + 1:
                raise RuntimeError("NonceOutOfOrder: stored %s, given %s"
                                   % (stored_nonce, n))
            stored_nonce = n
        self._entries[k] = {"cid": cid, "version": cur + 1, "updatedAt": 0,
                            "nonce": stored_nonce}
        self._nonce[k] = self.relay_nonce(enclave, key_hash) + 1
        kh = self._kh(key_hash)
        if not entry:
            self._refs.append([str(enclave), kh])
        self._seq += 1
        # One transaction per landing; the log index counts the landings
        # already recorded at the head block.
        self._logs.append({
            "enclave": str(enclave), "key": kh, "cid": cid,
            "version": cur + 1, "seq": self._seq, "nonce": stored_nonce,
            "blockNumber": self._block,
            "transactionHash": hashlib.sha256(
                b"esr-local commit %d" % self._seq).hexdigest(),
            "logIndex": sum(1 for l in self._logs if l["blockNumber"] == self._block),
        })
        self._persist()

    def relay(self, auth):
        """The node's relay of a staged esr.commit.<key16>.<relayNonce>.json
        authorization: commitFor. The authorization's relayNonce must equal
        the registry's next relay nonce for (enclave, key) or
        'RelayNonceMismatch' is raised (the contract's error) and nothing
        changes; then the commit applies as apply_commit does."""
        enclave = auth["enclave"]
        key_hash = bytes.fromhex(self._kh(auth["keyHash"]))
        expected = self.relay_nonce(enclave, key_hash)
        given = int(auth["relayNonce"])
        if given != expected:
            raise RuntimeError("RelayNonceMismatch: expected %s, given %s"
                               % (expected, given))
        self.apply_commit(enclave, key_hash, auth["cid"],
                          int(auth["expectedVersion"]), int(auth.get("nonce", 0)))


# ---- web3 stand-in ---------------------------------------------------------

class MemWeb3:
    """Just enough of a web3 to satisfy ecld_state._contract(): eth.contract
    binds the registry, eth.block_number is the registry's chain head and
    eth.get_logs is the registry's eth_getLogs."""

    def __init__(self, reg):
        self._reg = reg

        class _Eth:
            def contract(_self, address=None, abi=None):
                return _Contract(reg)

            @property
            def block_number(_self):
                return reg.block_number

            def get_block_number(_self):
                return reg.block_number

            def get_logs(_self, filter_params):
                return reg.get_logs(filter_params)
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
    reg = MemRegistry(file=reg_file, address=contract_address)
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
