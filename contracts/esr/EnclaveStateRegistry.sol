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
///         (2026-08-07), extended with commitFor (meta-transaction relay) so
///         node operators can sponsor gas without becoming the on-chain author.
///         Vendored here so the SDK ships the ABI it builds against.
contract EnclaveStateRegistry {
    struct StateEntry {
        string  cid;        // IPFS CID of the encrypted state blob
        uint256 version;    // monotonic; 0 = never committed, first commit => 1
        uint64  updatedAt;  // block timestamp of last commit
    }

    // enclave (author) => application key => entry
    mapping(address => mapping(bytes32 => StateEntry)) private _state;

    // enclave (author) => next expected relay nonce. Only consumed by
    // commitFor; direct commit() is naturally ordered by the account nonce of
    // the enclave's own transaction and does not touch this.
    mapping(address => uint256) private _relayNonce;

    event StateCommitted(
        address indexed enclave,
        bytes32 indexed key,
        string  cid,
        uint256 version
    );

    error VersionMismatch(uint256 expected, uint256 actual);
    error EmptyCID();
    error BadSignature();
    error RelayNonceMismatch(uint256 expected, uint256 actual);

    /// @notice Direct commit: the enclave sends and pays for its own transaction.
    /// @param key             App-defined key (e.g. keccak256(familyId)).
    /// @param newCID          IPFS CID of the new encrypted state blob.
    /// @param expectedVersion Version the caller based this update on (0 = first
    ///                        commit). Must equal the stored version or revert.
    function commit(bytes32 key, string calldata newCID, uint256 expectedVersion) external {
        _commit(msg.sender, key, newCID, expectedVersion);
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
    function commitFor(
        address enclave,
        bytes32 key,
        string calldata newCID,
        uint256 expectedVersion,
        uint256 relayNonce,
        bytes calldata signature
    ) external {
        uint256 expectedNonce = _relayNonce[enclave];
        if (relayNonce != expectedNonce) revert RelayNonceMismatch(expectedNonce, relayNonce);

        bytes32 digest = commitDigest(enclave, key, newCID, expectedVersion, relayNonce);
        address signer = _recover(digest, signature);
        if (signer == address(0) || signer != enclave) revert BadSignature();

        unchecked { _relayNonce[enclave] = expectedNonce + 1; }
        _commit(enclave, key, newCID, expectedVersion);
    }

    /// @notice The exact digest an enclave must sign for commitFor. Exposed so
    ///         the SDK derives the signature the same way the contract verifies
    ///         it, with no room for drift.
    function commitDigest(
        address enclave,
        bytes32 key,
        string memory newCID,
        uint256 expectedVersion,
        uint256 relayNonce
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
                relayNonce
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

    function exists(address enclave, bytes32 key) external view returns (bool) {
        return _state[enclave][key].version != 0;
    }

    // --- internal -----------------------------------------------------------

    function _commit(address enclave, bytes32 key, string calldata newCID, uint256 expectedVersion) private {
        if (bytes(newCID).length == 0) revert EmptyCID();
        StateEntry storage e = _state[enclave][key];
        if (e.version != expectedVersion) revert VersionMismatch(expectedVersion, e.version);
        unchecked { e.version = expectedVersion + 1; }
        e.cid = newCID;
        e.updatedAt = uint64(block.timestamp);
        emit StateCommitted(enclave, key, newCID, e.version);
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
