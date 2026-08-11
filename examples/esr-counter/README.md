# esr-counter — Enclave State Registry end-to-end example

A minimal dApp that exercises the whole ESR path: encrypted state written by the
enclave, relayed and paid for by the node, pointed to on-chain, and read back on
the next run.

Use it to verify an ESR deployment, or as the smallest working reference for
using `StateRegistry` in your own backend.

## What it does

```python
from ecld_state import StateRegistry

state = StateRegistry()
state.commit("e2e-counter", lambda s: {**s, "n": s.get("n", 0) + 1})
```

Run `esr_increment` twice: the counter advances and the on-chain version
increments. That is the proof state actually persisted and was read back — not
just that a call returned.

## Functions

| Function | Touches chain | Purpose |
|---|---|---|
| `esr_selftest()` | no | Wallet derives, encryption round-trips, CID matches a known IPFS vector |
| `esr_address()` | no | The enclave's on-chain identity — the address commits are filed under |
| `esr_read(key)` | no | Read state without writing |
| `esr_increment(key)` | **yes** | Read-modify-write, commits on-chain |

Start with `esr_selftest`. It touches no chain, so if it fails the problem is the
enclave build; if only `esr_increment` fails, the problem is the chain or the
operator (see below).

## Running it

```bash
ecld-build      # bakes ESR_CONTRACT_ADDRESS in; fails if the network has none
ecld-publish
```

That's it — **nothing to fund**. The node that runs your task relays each commit
and pays the gas, so a published dApp is autonomous. `.config.json` here leaves
`ESR.contract_address` empty on purpose, to show that `ecld-build` resolves the
canonical per-network address itself; hand-wiring it is what caused the
empty-result class of bug this replaces.

Run `esr_increment` on the same operator that runs the ESR stack; the node
relays the commit and the version advances.

## State is enclave-private

Every function returns plain data because the client **cannot decrypt** the
state: it is encrypted with a key derived from the enclave identity. Anything
the caller should see must be returned explicitly, which is the intended
pattern — the payload decides what leaves the enclave.

A client can still observe *metadata* (version, `updatedAt`) via `ESRContract`
in either runner, and wait for a commit to land with `waitForVersion`.

## Networks

ESR is deployed on Bloxberg (mainnet and testnet share one chain, so both use
the same registry) and LitVM LiteForge. On a network without a deployment,
`ecld-build` fails rather than sealing an empty address into the image.

## Caveat on testnet

The testnet enclave identity is reproducible by anyone who runs the published
image — there is no attestation. So testnet state is readable by anyone who
reproduces the identity. Treat testnet state as functional testing only, and do
not store real user data in it.
