# Managing dApp State (ESR)

By default a task is stateless: it receives an input, computes, returns a result, and forgets everything. The **Enclave State Registry (ESR)** gives your dApp durable state that persists between tasks — encrypted inside the enclave, stored on IPFS, and pointed to on-chain.

The enclave is the only thing that can read it. That is the point: your state is not visible to the node running your task, to other dApps, or to anyone reading the blockchain.

## How it works

```
your backend            enclave (SGX)              chain
------------            -------------              -----
state.commit(...)  -->  encrypt state
                        compute the CID
                        publish to storage   -->   commit(key, cid, version)
                                                   (node pins the content)
```

Only a **pointer** goes on-chain — the content stays encrypted. Each key has a version that increments on every commit.

## Enabling it

Run `ecld-init` and answer **yes** at the Enclave State Registry step, or add this to `.config.json`:

```json
"ESR": {
  "enabled": true,
  "contract_address": "",
  "autofund": { "enabled": false }
}
```

Leave `contract_address` empty. `ecld-build` fills in the correct registry for your network automatically.

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
| `wallet_address` | The enclave's on-chain address — see funding below |

`commit` uses optimistic concurrency. If another task commits between your read and your write, it re-reads and retries automatically, so parallel tasks cannot silently overwrite each other.

## Funding the enclave

The enclave pays for its own commit transactions, so it needs its own funds.

When you run `ecld-publish`, it prints:

```
✔  ESR wallet address: 0x...
   This wallet starts EMPTY. Fund it from your own wallet with whatever
   your payload needs; publishing never transfers value on its own.
```

Send a small amount of gas to that address. Without it, reads work but every write fails.

{% hint style="warning" %}
**The address changes when your enclave changes.** It is derived from the enclave's identity, so any SDK upgrade or backend edit produces a new address that must be funded again. Funds left at the previous address stay there. `ecld-publish` warns you when the address changes.
{% endhint %}

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

Everything under one key is read and written together, so avoid putting unrelated data in the same key: two tasks touching different parts of it will contend over the same version.

## Testnet warning

{% hint style="danger" %}
On testnet there is **no enclave attestation**, which means the enclave identity — and therefore its wallet and its state encryption key — can be reproduced by anyone who runs your published image.

Treat testnet state as functional testing only:

* do **not** fund testnet enclave wallets with real value
* do **not** store real user data in testnet state
{% endhint %}

## Troubleshooting

| What you see | What it means |
|---|---|
| `ESR is enabled but no ESR registry is deployed on <network>` | That network has no registry. Build for a supported network. |
| Task returns `CONFIG_ERROR` (32) | The enclave was built without a required value. Re-run `ecld-build`. |
| Reads work, writes fail | The enclave wallet is out of gas — fund the address from `ecld-publish`. |
| `holds a pointer that is not a CID` | A previous version of your code wrote an invalid pointer. |

## A complete example

The SDK ships a working end-to-end example at `examples/esr-counter`. Its `esr_selftest()` function needs no gas and no chain access, so it is the fastest way to confirm your enclave is built correctly before you start debugging funding.
