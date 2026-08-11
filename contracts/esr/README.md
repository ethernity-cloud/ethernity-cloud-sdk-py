# Enclave State Registry (ESR) — reference contract

Vendored copy of the contract the SDK builds against, from *"Design Proposal:
Enclave State Registry (ESR) — Smart Contract"* (2026-08-07). Shipped here so the
SDK owns the ABI it encodes calls with; it is **not** redeployed per project.

## What it does

An enclave publishes a pointer (IPFS CID) to its latest **encrypted** off-chain
state, per key, with monotonic versioning. Clients read the pointer to find and
sync the current state. The chain stores only the pointer and version — never the
state itself.

```
Enclave (SGX)                      ESR contract                Client
─────────────                      ────────────                ──────
derive wallet from identity  ──▶   commit(key, cid, expVer)
encrypt state, pin to IPFS         state[enclave][key]
                                   emit StateCommitted   ──▶    getState(enclave, key)
                                                                fetch CID, decrypt
```

## Deployed instances

| Network | Address |
|---|---|
| bloxberg mainnet | `0x4f6c0Ae54567CAeD372d265fEF412C2B5ed1302A` |
| bloxberg testnet | `0x4f6c0Ae54567CAeD372d265fEF412C2B5ed1302A` |
| LitVM LiteForge (4441) | `0xb0D2C139514C1B4e511c0eB83F22a842979B3ECa` |
| polygon / amoy / iotex / sepolia | *not deployed* |

Bloxberg mainnet and testnet are the **same chain** (both chainId 8995, both
reachable via `core.bloxberg.org` and `bloxberg.ethernity.cloud`) — they are
separated by different *protocol* contract addresses, not different networks.
The ESR above was verified present from both RPCs, so both point at that one
deployment. Deploying a second instance would spend real value duplicating a
contract on the same chain and split state across two registries.

Verified against the deployed testnet bytecode: all four function selectors are
present, the event topic matches, and the instance has live `StateCommitted`
history. The selectors computed from `EnclaveStateRegistry.abi` in this directory
match that deployment exactly — worth re-checking if the ABI is ever edited,
since a mismatch fails silently against a live contract.

## Design decisions (settled)

| Question | Decision | Why |
|---|---|---|
| `string cid` vs `bytes32` multihash | **keep `string`** | ABI-compatible with the live deployment and its existing commit history. The `bytes32` gas win is real but small, only works cleanly for CIDv0, and would break every existing caller. Offer as a v2 variant, never the default. |
| Immutable / ownerless | **yes** | An upgrade admin who could rewrite state would invalidate the entire trust model. To change it, deploy a new instance and re-commit (state is per-enclave and re-committable). |
| Canonical per-network address | **ship it in the SDK** | Hand-wiring the address is exactly the gap that caused StarChart's empty-result bug. The address should be injected at build time, like every other enclave constant. |
| `commitBatch` in v1 | **no** | Speculative until a real enclave needs multi-key atomicity. |
| `updatedAt` in the event | **no** | Derivable from the block; emitting it costs gas for redundant data. |

## Trust model — read this before funding anything

The contract **cannot** verify SGX attestation (nothing on-chain can). Isolation
is structural: writes only ever touch `_state[msg.sender][...]`, so one enclave
physically cannot overwrite another's slot, and a rogue committer only writes junk
under its own address. `expectedVersion` gives optimistic concurrency and replay
protection — an old commit fails because the stored version has moved on.

Everything therefore reduces to one question: **is the enclave wallet key secret?**

- **mainnet** — the identity is CAS-provisioned behind attestation, so the key is
  effectively enclave-only. This is the intended posture.
- **testnet** — there is **no attestation**. An attacker pulls the published
  enclave image from IPFS and runs it; nothing verifies the enclave, so it derives
  the same identity key and the same wallet. Note this is *not* fixed by the key
  derivation living inside the compiled `.so` — the attacker does not reverse the
  binary, they run it. Testnet ESR wallets are therefore drainable and are for
  functional testing only.

What would genuinely close the testnet gap: attestation on testnet, or a
CPU-bound secret input (`EGETKEY` / sealing key) that never leaves the processor,
so a rebuilt image on different hardware derives a *different* key. Neither
exists today.
