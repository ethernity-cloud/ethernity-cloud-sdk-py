// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title  EnclaveStateRegistry (ESR)
/// @notice On-chain registry for Ethernity Cloud enclaves to publish pointers
///         (IPFS CIDs) to their latest encrypted off-chain state, namespaced
///         per enclave (msg.sender) and per key, with monotonic versioning and
///         optimistic concurrency.
/// @dev    The committer (msg.sender) is the enclave identity. The contract does
///         NOT verify SGX attestation; trust derives from the enclave wallet key
///         existing only inside the genuine enclave (CAS-provisioned on mainnet).
///
///         Reference copy of the contract described in
///         "Design Proposal: Enclave State Registry (ESR) — Smart Contract"
///         (2026-08-07). Vendored here so the SDK ships the ABI it builds
///         against; it is NOT redeployed per project.
///
///         Deployed instances:
///           bloxberg testnet  0x4f6c0Ae54567CAeD372d265fEF412C2B5ed1302A
///
///         Verified against the deployed bytecode: all four function selectors
///         (commit/getState/getVersion/exists) are present, and the instance has
///         live StateCommitted history.
contract EnclaveStateRegistry {
    struct StateEntry {
        string  cid;        // IPFS CID of the encrypted state blob
        uint256 version;    // monotonic; 0 = never committed, first commit => 1
        uint64  updatedAt;  // block timestamp of last commit
    }

    // enclave (committer) => application key => entry
    mapping(address => mapping(bytes32 => StateEntry)) private _state;

    event StateCommitted(
        address indexed enclave,
        bytes32 indexed key,
        string  cid,
        uint256 version
    );

    error VersionMismatch(uint256 expected, uint256 actual);
    error EmptyCID();

    /// @param key             App-defined key (e.g. keccak256(familyId)).
    /// @param newCID          IPFS CID of the new encrypted state blob.
    /// @param expectedVersion Version the caller based this update on (0 = first
    ///                        commit). Must equal the stored version or revert.
    function commit(bytes32 key, string calldata newCID, uint256 expectedVersion) external {
        if (bytes(newCID).length == 0) revert EmptyCID();
        StateEntry storage e = _state[msg.sender][key];
        if (e.version != expectedVersion) revert VersionMismatch(expectedVersion, e.version);
        unchecked { e.version = expectedVersion + 1; }
        e.cid = newCID;
        e.updatedAt = uint64(block.timestamp);
        emit StateCommitted(msg.sender, key, newCID, e.version);
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
}
