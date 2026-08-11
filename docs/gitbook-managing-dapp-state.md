# Managing dApp State (ESR)

By default a task is stateless: it receives an input, computes, returns a result, and forgets everything. The **Enclave State Registry (ESR)** gives your dApp durable state that persists between tasks — encrypted inside the enclave, stored on IPFS, and pointed to on-chain.

The enclave is the only thing that can read it. That is the point: your state is not visible to the node running your task, to other dApps, or to anyone reading the blockchain.

## How it works

```
your backend            enclave (SGX)                 node                chain
------------            -------------                 ----                -----
state.commit(...)  -->  encrypt state
                        compute the CID
                        SIGN commit(key, cid, ver) --> submit + PAY  -->  commit recorded
                        stage blob for pinning         pin the blob        under the enclave
```

Only a **pointer** goes on-chain — the content stays encrypted. Each key has a version that increments on every commit.

You don't fund anything and you don't pay gas. The enclave **signs** each commit; the **node submits it and pays**. The commit is still recorded on-chain *as the enclave's* — the node can relay it or not, but it cannot forge, alter, or misattribute what your enclave signed.

## Enabling it

Run `ecld-init` and answer **yes** at the Enclave State Registry step, or add this to `.config.json`:

```json
"ESR": {
  "enabled": true,
  "contract_address": ""
}
```

Leave `contract_address` empty. `ecld-build` fills in the correct registry for your network automatically. There is nothing else to configure — no wallet, no funding, no keys.

## Using it in your backend

```python
from ecld_state import StateRegistry

def get_profile(user_id):
    return StateRegistry().get(f"profile-{user_id}")

def add_score(user_id, points):
    state = StateRegistry()
    return state.commit(
        f"profile-{user_id}",
        lambda s: {**s, "score": s.get("score", 0) + points},
    )
```

For Nodenithy (JavaScript) the API is the same:

```javascript
const { StateRegistry } = require('./ecld_state');

async function addScore(userId, points) {
  const state = new StateRegistry();
  return state.commit(`profile-${userId}`, (s) => ({
    ...s,
    score: (s.score || 0) + points,
  }));
}
```

### The API

| Call | What it does |
|---|---|
| `get(key)` | Returns the decrypted state, or `{}` if nothing was ever stored |
| `commit(key, mutate)` | Read-modify-write: `mutate` receives the current state and returns the new one |
| `get_version(key)` | The current version number; `0` means never written |
| `wallet_address` | The enclave's on-chain identity — the address commits are recorded under |

`commit` uses optimistic concurrency. If another task commits between your read and your write, it re-reads and retries automatically, so parallel tasks cannot silently overwrite each other.

## Who pays for commits

Nobody on your side. The node that runs your task **relays each commit and pays the gas** — so a dApp is autonomous once published, with no wallet to top up.

To keep a runaway or hostile payload from spending an operator's money, each order has a **cumulative gas budget** for its state commits. If your task's commits would exceed it, the order fails with **`ESR_GAS_LIMIT_EXCEEDED` (34)** instead of overspending. In practice this only bites pathological cases — writing enormous state, or committing in a tight loop. Keep state small and commit deliberately (see *Designing your keys*) and you will never approach it.

`wallet_address` is still useful: it is the on-chain identity your state is filed under, so a frontend reads state and metadata by that address (below).

## Reading state from your frontend

Your frontend **cannot** decrypt the state — only the enclave can. To show state in your UI, return it from a function:

```python
def get_dashboard():
    state = StateRegistry().get("dashboard")
    return {"score": state.get("score", 0)}   # you choose what is exposed
```

This is deliberate. You decide exactly what leaves the enclave, rather than exposing your whole state to anyone who can read IPFS.

Your frontend can still observe *metadata* — useful for knowing when a task's changes have landed:

```javascript
import ESRContract from '@ethernity-cloud/runner/contract/operation/esrContract';

const esr = new ESRContract(registryAddress, walletContext);
const { version } = await esr.getState(enclaveAddress, 'dashboard');

// after submitting a task, wait for the state to actually change
await esr.waitForVersion(enclaveAddress, 'dashboard', version);
```

## Designing your keys

A key is any string — `keccak256` of it identifies the slot on-chain. Use one key per independent thing so unrelated writes never conflict:

- `profile-<user_id>` — one slot per user
- `game-<match_id>` — one slot per match
- `config` — a single global slot

Everything under one key is read and written together, so avoid putting unrelated data in the same key: two tasks touching different parts of it will contend over the same version. Small, focused keys also keep each commit cheap, which keeps you comfortably under the per-order gas budget.

## Testnet warning

{% hint style="danger" %}
On testnet there is **no enclave attestation**, which means the enclave identity — and therefore its state encryption key — can be reproduced by anyone who runs your published image. Anyone reproducing the identity can read your testnet state.

Treat testnet state as functional testing only — do **not** store real user data in it.
{% endhint %}

## Troubleshooting

| What you see | What it means |
|---|---|
| `ESR is enabled but no ESR registry is deployed on <network>` | That network has no registry. Build for a supported network. |
| Task returns `CONFIG_ERROR` (32) | The enclave was built without a required value. Re-run `ecld-build`. |
| Task returns `ESR_GAS_LIMIT_EXCEEDED` (34) | The order's commits would exceed its gas budget. Commit less, or smaller state. |
| `holds a pointer that is not a CID` | A previous version of your code wrote an invalid pointer. |

## A complete example

The SDK ships a working end-to-end example at `examples/esr-counter`. Its `esr_selftest()` function touches no chain, so it is the fastest way to confirm your enclave is built correctly: run it first, then `esr_increment` twice and watch the version advance.
