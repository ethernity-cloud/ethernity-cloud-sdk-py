// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title  EnclaveStateRegistry (ESR)
/// @notice On-chain registry for Ethernity Cloud enclaves to publish pointers
///         (IPFS CIDs) to their latest encrypted off-chain state, namespaced
///         per enclave and per key, with monotonic versioning and optimistic
///         concurrency.
/// @dev    An enclave's identity is its wallet key, which exists only inside the
///         genuine enclave (CAS-provisioned on mainnet). The contract does NOT
///         verify SGX attestation; trust derives from that key's secrecy.
///
///         Two ways to commit, both authored BY the enclave:
///           - commit(...)     : the enclave sends its own tx (msg.sender is the
///                               enclave; the enclave pays gas).
///           - commitFor(...)  : the enclave SIGNS the commit, and any relayer
///                               (typically the node) submits it and pays gas.
///                               State is stored under the RECOVERED signer, so
///                               the relayer cannot forge, alter, or misattribute
///                               a commit -- it can only choose to broadcast, or
///                               not, exactly what the enclave signed.
///
///         Reference copy of the contract described in
///         "Design Proposal: Enclave State Registry (ESR) — Smart Contract"
///         (2026-08-07), extended with:
///           - commitFor (meta-transaction relay) so node operators can sponsor
///             gas without becoming the on-chain author, and
///           - an ENUMERATION + GLOBAL SEQUENCE layer (2026-08-12) so replicator
///             nodes can mirror EVERY enclave's state reliably without scanning
///             event logs. The original mapping is not enumerable, so a
///             replicator previously had to scan StateCommitted logs (which time
///             out over large block ranges and need off-chain cursors). Now the
///             contract itself exposes the full entry set and a monotonic
///             commitSeq, so a replicator resumes from a sequence number with a
///             single call and can never miss or re-scan.
///         Vendored here so the SDK ships the ABI it builds against.
///
///         Backward compatibility: every pre-existing function keeps its exact
///         signature and behaviour. The additions are new view functions, a new
///         `seq` field appended to StateCommitted, and internal bookkeeping.
///         Entries committed to an OLDER deployment are readable there but only
///         become enumerable here once (re)committed to this contract.
contract EnclaveStateRegistry {
    struct StateEntry {
        string  cid;        // IPFS CID of the encrypted state blob
        uint256 version;    // monotonic; 0 = never committed, first commit => 1
        uint64  updatedAt;  // block timestamp of last commit
        // Last accepted idempotency nonce for this (enclave, key); 0 = none yet.
        // PUBLIC DATA: anyone can read it (getNonce), by design -- clients derive
        // their next nonce from it with a free eth_call: next = getNonce() + 1.
        // Enforced strictly sequential: a commit carrying nonce != 0 must be
        // EXACTLY the stored value + 1 (no gaps, no reuse); nonce == 0 means
        // "no idempotency guard on this commit" and PRESERVES the stored value.
        uint256 nonce;
    }

    /// @dev A stable (enclave, key) identifier for enumeration. `enclave` IS the
    ///      enclave's ESR identity address (it signs commits with its identity
    ///      key), so (enclave, key) is the unambiguous identity a replicator
    ///      tracks progress under.
    struct EntryRef {
        address enclave;
        bytes32 key;
    }

    // enclave (author) => application key => entry
    mapping(address => mapping(bytes32 => StateEntry)) private _state;

    // enclave (author) => next expected relay nonce. Only consumed by
    // commitFor; direct commit() is naturally ordered by the account nonce of
    // the enclave's own transaction and does not touch this.
    mapping(address => uint256) private _relayNonce;

    // --- enumeration + global sequence (replication support) ----------------

    // Append-only list of every distinct (enclave, key) ever committed, in
    // first-commit order. Never reordered or removed, so an index is a stable
    // handle. Enables a replicator to enumerate the full state set directly.
    EntryRef[] private _entries;

    // (enclave => key => 1-based index into _entries, 0 = not present) so a
    // repeated commit for an existing (enclave, key) does not append a duplicate.
    mapping(address => mapping(bytes32 => uint256)) private _entryIndex1;

    // Monotonic counter incremented on EVERY commit (not just first-time
    // entries). A replicator that remembers the last seq it processed resumes
    // from there with no event scanning and no block-range limits.
    uint256 public commitSeq;

    event StateCommitted(
        address indexed enclave,
        bytes32 indexed key,
        string  cid,
        uint256 version,
        uint256 seq,         // global monotonic sequence of this commit
        uint256 nonce        // idempotency nonce carried by this commit (0 = none)
    );

    error VersionMismatch(uint256 expected, uint256 actual);
    error EmptyCID();
    error BadSignature();
    error RelayNonceMismatch(uint256 expected, uint256 actual);
    error NonceOutOfOrder(uint256 stored, uint256 given);

    /// @notice Direct commit: the enclave sends and pays for its own transaction.
    /// @param key             App-defined key (e.g. keccak256(familyId)).
    /// @param newCID          IPFS CID of the new encrypted state blob.
    /// @param expectedVersion Version the caller based this update on (0 = first
    ///                        commit). Must equal the stored version or revert.
    /// @param nonce           Idempotency nonce; must be EXACTLY the stored nonce
    ///                        for this (enclave, key) + 1, or 0 to skip the guard
    ///                        and preserve the stored value.
    function commit(bytes32 key, string calldata newCID, uint256 expectedVersion, uint256 nonce) external {
        _commit(msg.sender, key, newCID, expectedVersion, nonce);
    }

    /// @notice Relayed commit: any sender submits a commit the enclave SIGNED,
    ///         and pays the gas. The commit is attributed to the recovered
    ///         signer, never to msg.sender, so a node can sponsor gas but cannot
    ///         forge or alter what an enclave publishes.
    /// @dev    The signed digest binds every field plus the relay nonce, this
    ///         contract's address, and the chain id, so a signature cannot be
    ///         replayed across keys, versions, contracts, chains, or twice.
    /// @param enclave         The enclave the commit is attributed to; must equal
    ///                        the address recovered from the signature.
    /// @param key             App-defined key.
    /// @param newCID          IPFS CID of the new encrypted state blob.
    /// @param expectedVersion Optimistic-concurrency version (see commit).
    /// @param relayNonce      Must equal the enclave's current relay nonce.
    /// @param signature       65-byte secp256k1 signature (r,s,v) over the digest
    ///                        from the enclave key.
    /// @param nonce Idempotency nonce (signature-bound): EXACTLY the stored
    ///               nonce for this (enclave, key) + 1, or 0 to skip the guard
    ///               and preserve the stored value. Bound into the signed
    ///               digest, so the relayer cannot alter it.
    function commitFor(
        address enclave,
        bytes32 key,
        string calldata newCID,
        uint256 expectedVersion,
        uint256 relayNonce,
        uint256 nonce,
        bytes calldata signature
    ) external {
        uint256 expectedNonce = _relayNonce[enclave];
        if (relayNonce != expectedNonce) revert RelayNonceMismatch(expectedNonce, relayNonce);

        bytes32 digest = commitDigest(enclave, key, newCID, expectedVersion, relayNonce, nonce);
        address signer = _recover(digest, signature);
        if (signer == address(0) || signer != enclave) revert BadSignature();

        unchecked { _relayNonce[enclave] = expectedNonce + 1; }
        _commit(enclave, key, newCID, expectedVersion, nonce);
    }

    /// @notice The exact digest an enclave must sign for commitFor. Exposed so
    ///         the SDK derives the signature the same way the contract verifies
    ///         it, with no room for drift. The idempotency nonce is part of the
    ///         digest: a relayer can only broadcast, or not, exactly what the
    ///         enclave signed.
    function commitDigest(
        address enclave,
        bytes32 key,
        string memory newCID,
        uint256 expectedVersion,
        uint256 relayNonce,
        uint256 nonce
    ) public view returns (bytes32) {
        // Domain-separated so a signature is valid only for THIS contract on
        // THIS chain: block.chainid + address(this) are inside the hash.
        return keccak256(
            abi.encode(
                "EnclaveStateRegistry.commitFor",
                block.chainid,
                address(this),
                enclave,
                key,
                keccak256(bytes(newCID)),
                expectedVersion,
                relayNonce,
                nonce
            )
        );
    }

    /// @notice Current relay nonce for an enclave (0 if it has never used
    ///         commitFor). The SDK reads this to build the next signature.
    function relayNonce(address enclave) external view returns (uint256) {
        return _relayNonce[enclave];
    }

    function getState(address enclave, bytes32 key)
        external view returns (string memory cid, uint256 version, uint64 updatedAt)
    {
        StateEntry storage e = _state[enclave][key];
        return (e.cid, e.version, e.updatedAt);
    }

    function getVersion(address enclave, bytes32 key) external view returns (uint256) {
        return _state[enclave][key].version;
    }

    /// @notice Last accepted idempotency nonce for (enclave, key); 0 if none was
    ///         ever supplied. PUBLIC by design: clients derive their next nonce
    ///         from this with a free eth_call -- the next value is always
    ///         exactly `getNonce() + 1`.
    function getNonce(address enclave, bytes32 key) external view returns (uint256) {
        return _state[enclave][key].nonce;
    }

    function exists(address enclave, bytes32 key) external view returns (bool) {
        return _state[enclave][key].version != 0;
    }

    // --- enumeration API (replication support) ------------------------------

    /// @notice Number of distinct (enclave, key) entries ever committed. Stable
    ///         and append-only, so indices [0, entryCount()) are permanent
    ///         handles a replicator can iterate.
    function entryCount() external view returns (uint256) {
        return _entries.length;
    }

    /// @notice The (enclave, key) at an enumeration index.
    function entryAt(uint256 index)
        external view returns (address enclave, bytes32 key)
    {
        EntryRef storage r = _entries[index];
        return (r.enclave, r.key);
    }

    /// @notice Batch read of entries WITH their current state, starting at
    ///         `startIndex` for up to `limit` entries. One call gives a
    ///         replicator everything it needs to mirror a slice of the registry
    ///         -- no event scanning, no per-entry getState round-trips. Returns
    ///         the slice and the total count so the caller knows when it is done.
    /// @dev    Arrays are returned parallel: enclaves[i]/keys[i]/cids[i]/
    ///         versions[i]/updatedAts[i] all describe the same entry.
    function getEntriesFrom(uint256 startIndex, uint256 limit)
        external
        view
        returns (
            address[] memory enclaves,
            bytes32[] memory keys,
            string[]  memory cids,
            uint256[] memory versions,
            uint64[]  memory updatedAts,
            uint256   total
        )
    {
        total = _entries.length;
        if (startIndex >= total || limit == 0) {
            return (
                new address[](0),
                new bytes32[](0),
                new string[](0),
                new uint256[](0),
                new uint64[](0),
                total
            );
        }
        uint256 end = startIndex + limit;
        if (end > total) end = total;
        uint256 n = end - startIndex;

        enclaves    = new address[](n);
        keys        = new bytes32[](n);
        cids        = new string[](n);
        versions    = new uint256[](n);
        updatedAts  = new uint64[](n);

        for (uint256 i = 0; i < n; i++) {
            EntryRef storage r = _entries[startIndex + i];
            StateEntry storage e = _state[r.enclave][r.key];
            enclaves[i]   = r.enclave;
            keys[i]       = r.key;
            cids[i]       = e.cid;
            versions[i]   = e.version;
            updatedAts[i] = e.updatedAt;
        }
    }

    // --- internal -----------------------------------------------------------

    function _commit(address enclave, bytes32 key, string calldata newCID, uint256 expectedVersion, uint256 nonce) private {
        if (bytes(newCID).length == 0) revert EmptyCID();
        StateEntry storage e = _state[enclave][key];
        if (e.version != expectedVersion) revert VersionMismatch(expectedVersion, e.version);
        // Idempotency guard: for the same (enclave, key), nonces are accepted
        // strictly sequentially -- exactly stored + 1, no gaps, no reuse.
        // nonce == 0 opts out and preserves the stored value, so a plain
        // commit can never reset the guard.
        if (nonce != 0) {
            if (nonce != e.nonce + 1) revert NonceOutOfOrder(e.nonce, nonce);
            e.nonce = nonce;
        }
        unchecked { e.version = expectedVersion + 1; }
        e.cid = newCID;
        e.updatedAt = uint64(block.timestamp);

        // Register the (enclave, key) in the enumerable index the first time it
        // is ever committed (version transitions 0 -> 1). Repeat commits update
        // the existing entry in place, so the index stays duplicate-free.
        if (_entryIndex1[enclave][key] == 0) {
            _entries.push(EntryRef({enclave: enclave, key: key}));
            _entryIndex1[enclave][key] = _entries.length; // 1-based
        }

        // Advance the global sequence on every commit and stamp it into the
        // event, so a replicator can resume strictly after the last seq it saw.
        unchecked { commitSeq += 1; }
        emit StateCommitted(enclave, key, newCID, e.version, commitSeq, nonce);
    }

    /// @dev Recovers the signer of an eth_sign-style prefixed message hash. The
    ///      enclave signs the prefixed digest (matching eth-account's
    ///      sign_message / EIP-191), which is what the SDK produces.
    function _recover(bytes32 digest, bytes calldata sig) private pure returns (address) {
        if (sig.length != 65) return address(0);
        bytes32 ethHash = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", digest)
        );
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 32))
            v := byte(0, calldataload(add(sig.offset, 64)))
        }
        if (v < 27) v += 27;
        if (v != 27 && v != 28) return address(0);
        // Reject the upper half of the s range (EIP-2 malleability guard).
        if (uint256(s) > 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0) {
            return address(0);
        }
        return ecrecover(ethHash, v, r, s);
    }
}
