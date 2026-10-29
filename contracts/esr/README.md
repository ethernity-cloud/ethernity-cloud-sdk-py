# Enclave State Registry (ESR) — reference contract

Vendored copy of the contract the SDK builds against. Shipped here so the SDK
owns the ABI it encodes calls with; it is **not** redeployed per project.

## What it does

An enclave publishes a pointer (IPFS CID) to its latest **encrypted** off-chain
state, per key, with monotonic versioning. Clients read the pointer to find and
sync the current state. The chain stores only the pointer and version — never the
state itself.

The enclave never pays gas. It **signs** each commit; the **node** submits it and
pays (`commitFor`), and the registry records the commit under the *enclave's*
address, recovered from the signature. So a published dApp needs no funded wallet,
and the node can relay a commit or not — it cannot forge, alter, or misattribute
what the enclave signed.

```
Enclave (SGX)                    Node (relayer)            ESR contract        Client
─────────────                    ──────────────            ────────────        ──────
derive identity, sign commit ──▶ commitFor(sig) + PAY ──▶  recover signer
encrypt state, stage for pin     pin the blob              state[enclave][key]
                                                           emit StateCommitted ─▶ getState(enclave,key)
                                                                                  fetch CID, decrypt
```

## Interface

| Function | Who calls it | Purpose |
|---|---|---|
| `commit(key, cid, expectedVersion, nonce)` | the enclave, paying its own gas | direct write (self-paid path); `nonce` 0 lets the registry assign the next idempotency nonce, a non-zero value must be exactly `getNonce(enclave, key) + 1` |
| `commitFor(enclave, key, cid, expectedVersion, relayNonce, nonce, signature)` | any relayer (the node), paying | write a commit the enclave *signed*; recorded under the recovered signer |
| `commitDigest(enclave, key, cid, expectedVersion, relayNonce, nonce)` | off-chain | the exact digest the enclave signs, domain-separated by chain id and contract address; the SDK, the node, the trustedzone and the validators ask the contract for it rather than recomputing it |
| `relayNonce(enclave, key)` | off-chain | the relay nonce of that (enclave, key); each `commitFor` to the key consumes the next one (replay-safe), and different keys relay independently |
| `getNonce(enclave, key)` | off-chain | the last idempotency nonce accepted for (enclave, key), 0 when none; the next commit may pin `getNonce() + 1` |
| `getState(enclave, key)` / `getVersion(...)` / `exists(...)` | clients | read the pointer, version, timestamp |
| `entryCount()` / `entryAt(i)` / `getEntriesFrom(i, n)` / `commitSeq()` | replicators | enumerate every (enclave, key) entry and resume from the global commit sequence |

The relay path is the default the SDK uses. `commit()` remains for a self-paid
fallback; both attribute the write to the enclave.

## Deployed instances

| Network | Address |
|---|---|
| bloxberg mainnet | `0xF76469A5659670B6ade366dE635e6463aaB8f3D8` |
| bloxberg testnet | `0xF76469A5659670B6ade366dE635e6463aaB8f3D8` |
| LitVM LiteForge (4441) | `0xEF434486C0dbA37A9EaC8Ffe9A91190788D42054` |
| polygon / amoy / iotex / sepolia | *not deployed* |

Bloxberg mainnet and testnet are the **same chain** (both chainId 8995, both
reachable via `core.bloxberg.org` and `bloxberg.ethernity.cloud`) — they are
separated by different *protocol* contract addresses, not different networks, so
both point at the one deployment.

All function selectors were verified present against the deployed bytecode after
deploy, and the full relay flow (sign → relay → attribute under the enclave;
forged/tampered/replayed signatures rejected) was exercised live on both chains.
Re-check the selectors if `EnclaveStateRegistry.abi` here is ever edited — a
mismatch fails silently against a live contract.

## Design decisions (settled)

| Question | Decision | Why |
|---|---|---|
| Node-relayed commits (`commitFor`) | **yes, default** | The enclave signs; the node pays. Removes the funding step entirely and makes a published dApp autonomous. The signature binds every field, so the node cannot forge a commit. |
| Per-order gas cap on relayed commits | **node + trustedzone enforce** | The node refuses to relay past a per-order budget; the attested trustedzone independently re-prices the whole signed ledger and terminates the order (`ESR_GAS_LIMIT_EXCEEDED`) if breached — so a hostile payload cannot drain an operator, and nobody is trusted on cost. |
| `string cid` vs `bytes32` multihash | **keep `string`** | ABI-compatible with the live deployment and its commit history. The `bytes32` gas win is real but small and only works cleanly for CIDv0. |
| Immutable / ownerless | **yes** | An upgrade admin who could rewrite state would invalidate the entire trust model. To change it, deploy a new instance and re-commit (state is per-enclave and re-committable). |
| Canonical per-network address | **ship it in the SDK** | Hand-wiring the address is exactly the gap that caused the empty-result bug. It is injected at build time, like every other enclave constant. |

## Trust model

The contract **cannot** verify SGX attestation (nothing on-chain can). Isolation
is structural: `commitFor` stores state under the address **recovered from the
signature**, never `msg.sender`, so a relayer can only publish exactly what the
enclave signed — it cannot write under another enclave's address or change a
signed commit. `commit()` writes under `msg.sender` directly. Either way one
enclave physically cannot overwrite another's slot, and `expectedVersion` gives
optimistic concurrency and replay protection. The per-(enclave, key) `relayNonce`
blocks replay of a relayed commit, and the signature-bound idempotency `nonce`
lets a pinned commit apply at most once.

Everything therefore reduces to one question: **is the enclave identity key secret?**

- **mainnet** — the identity is CAS-provisioned behind attestation, so the key is
  effectively enclave-only. This is the intended posture.
- **testnet** — there is **no attestation**. An attacker pulls the published
  enclave image from IPFS and runs it; nothing verifies the enclave, so it derives
  the same identity key. Note this is *not* fixed by the key derivation living
  inside the compiled `.so` — the attacker does not reverse the binary, they run
  it. So testnet state is readable/writable by anyone who reproduces the identity,
  and is for functional testing only. (Because the node pays for commits, there is
  no wallet to drain — the exposure is on state confidentiality, not funds.)

What would genuinely close the testnet gap: attestation on testnet, or a
CPU-bound secret input (`EGETKEY` / sealing key) that never leaves the processor,
so a rebuilt image on different hardware derives a *different* key. Neither exists
today.
