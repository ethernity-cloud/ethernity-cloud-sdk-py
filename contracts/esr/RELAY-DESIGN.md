# ESR node-relayed commits — design

How ESR state commits reach the chain **without the enclave holding gas**, and
how a malicious payload is prevented from spending an operator's money.

## Why

The direct path (`commit()`, enclave pays) needs every enclave to fund a wallet
— friction, and a funding step that silently breaks writes when skipped. The
relay path makes ESR **autonomous**: the node pays, so there is nothing to fund.

But "the node pays" invites abuse: a custom/malicious enclave could sign huge or
endless state commits and drain the operator. So the node's spend is **capped
per order**, and a cap breach is adjudicated by the **attested trustedzone**,
not decided unilaterally by the untrusted node.

## Trust model (v3)

- **securelock** — runs the (untrusted) client payload. This is where a
  malicious `commit` originates.
- **trustedzone** — attested gatekeeper. Already verifies securelock's signed
  result/result_code before building the on-chain result. It is honest.
- **node** — pays gas. Untrusted: it must not be able to forge a commit or fake
  a termination, and it must be protected from being made to overpay.

## Mechanism

### 1. The contract: `commitFor` (deployed)

The enclave **signs** a commit; any relayer submits it and pays gas. State is
stored under the **recovered signer**, never `msg.sender`. Every field —
enclave, key, CID, expectedVersion, relay nonce, chainid, contract — is inside
the signed digest, so the relayer can only broadcast *exactly* what was signed.

Proven live on Bloxberg: tampered CID/key/version and relayer-impersonation all
revert `BadSignature`; a consumed nonce reverts `RelayNonceMismatch`; a legit
relay lands under the enclave with the node paying. The instances per network
are in README.md.

### 1b. The contract: `commitForBatch`

`commitForBatch(enclave, commits[])` relays several commits of one enclave in
one transaction. Any relayer sends it; each entry `{key, newCID,
expectedVersion, relayNonce, nonce, signature}` is applied in calldata order
exactly as `commitFor` applies one (the relay nonce checked against what the
entries before it left, the signer recovered and required to be `enclave`, the
relay nonce advanced, the state written) and emits its own `StateCommitted`.
The batch is all or nothing: the first entry that refuses reverts the whole
transaction with `BatchEntryFailed(index, reason)`, `reason` that entry's own
revert data (`RelayNonceMismatch`, `BadSignature`, `VersionMismatch`,
`NonceOutOfOrder` or `EmptyCID`); an entry whose `commitFor` frame runs out of
gas reverts it with `BatchEntryOutOfGas(index)` instead, since the empty
revert data of a starved frame is no refusal (the batch needs more gas than
the transaction gave it); an empty batch reverts `EmptyBatch`. In every case
no entry is applied. The gas reason: a batch pays one transaction's intrinsic
21,000 gas for all of its entries and its later entries find the slots the
earlier ones wrote warm, which is what a node bundling an order's commits
while the gas price is above the order's cap saves; the external self-call
that gives the index costs about 2,520 gas per entry (on the local Hardhat
network a one-entry batch of a key's first commit is 269,999 gas against
267,477 for the same commit through `commitFor`, and every further first
commit in the batch about 238,050). Same-key entries go in relay-nonce order,
which is the order the securelock signs them in. The trustedzone's
landed-commit check and the validators' proof read one `StateCommitted` per
entry, as from single transactions.

### 2. Streaming commits with a per-order gas cap

A payload may call `state.commit()` any number of times; **each is one on-chain
commit**. Commits stream: each is staged and relayed as it happens, and the node
keeps a **running per-order gas total**.

- **Cap: 0.1 POL per order (cumulative).** Flat for v1 — no swap-price valuation
  yet.
- **Testnet + Bloxberg**: gas is ~free; the node pays without valuation.
- **Mainnets**: the commit that would push the order total over 0.1 POL is
  **refused** — no refund, no silent overpay.

### 3. Who does the gas math

The **node** does the gas accounting — it is the payer, and it knows live fees.
The **securelock does none**: it only signs each commit and records the signed
authorization. Nothing the securelock could compute about cost would be trusted,
so it computes nothing. The **trustedzone** independently re-does the math as the
attested check on the node.

- **node** — for each staged authorization, estimates the `commitFor` gas ×
  live price, keeps a running per-order total, and **refuses to relay** the one
  that would cross the budget (protecting its own wallet). It leaves the refused
  authorization in the ledger for the trustedzone.
- **trustedzone** — re-computes the whole order's cost from the signed ledger
  (§4) and terminates if it exceeds the budget from its own sealed config.

Two independent payer-side/attested-side gates over the same signed facts.

### 4. Trustedzone adjudicates — and computes cost ITSELF

The securelock stages **every** commit it signs this order (relay nonce
0,1,2,…) into `esr.authorizations.json` — the complete, signature-bound ledger,
including any it refused for exceeding the cap.

The trustedzone (attested, unlike the node) then adjudicates, and it **trusts no
cost figure from anyone**. From the ledger it uses only the signature-bound
fields (enclave, key, cid, version, nonce, signature). For each authorization
whose signature recovers to the enclave, it **independently simulates the
commitFor gas on-chain** (`estimate_gas`) and multiplies by the live per-chain
gas price, summing across the **whole order**. It compares that self-computed
total against the budget from its **own sealed config**
(`ESR_RELAY_GAS_BUDGET_WEI`), never a number written by the payload. If the total
exceeds the budget it sets `ESR_GAS_LIMIT_EXCEEDED` (34), failing the order.

Why each property holds:

- **Node cannot fake a termination** — a forged ledger entry has a signature
  that does not recover to the enclave, so it is dropped; the total is computed
  only from genuine ones.
- **Malicious securelock cannot understate cost** to slip past the cap — the
  trustedzone ignores the securelock's claimed `gasUnits` and re-estimates every
  call itself.
- **…nor overstate it** to fabricate a termination — same reason; the number
  comes from on-chain estimation, not the payload.
- **Malicious payload cannot hide many small commits** — the ledger is the whole
  order's footprint (all nonces), and the cap is the **cumulative** self-computed
  total, not per-commit.
- **Node cannot force overpay** — it enforces the same cap independently and
  refuses to relay past it.

Belt-and-suspenders: the securelock refuses to authorize past the cap, the node
refuses to relay past it, and the trustedzone re-computes and terminates — three
independent gates, and the final verdict is authored by the attested enclave.

## Data flow

```
payload: state.commit() xN  (streaming)
  securelock, per commit (NO gas math):
    sign commitFor
    append signed auth to esr.authorizations.json (order-wide ledger)
    stage esr.commit.<nonce>.json for the node
  node, per staged auth (it pays, so it does the math):
    estimate gas x live price; running_total += cost
    if within budget:  submit commitFor (PAY)
    else:              refuse (leave it in the ledger)
  trustedzone (attested re-check of the node):
    read esr.authorizations.json
    drop entries whose signature does not recover to the enclave
    for the rest: estimate_gas x live price, summed over the WHOLE order
    compare that self-computed total to ESR_RELAY_GAS_BUDGET_WEI (own config)
    if breached: set FAILED result (ESR_GAS_LIMIT_EXCEEDED)  -> order ends
    else:        build result as normal
```

## Validator verification of the delegated commits

ESR commits are relayed by the untrusted node, so the **validator** (which
re-runs the trustedzone flow to check a node behaved) must be able to verify the
node relayed *exactly* what the enclave authorized — no dropped, added, or
altered commits. Three layers, none of which the node can forge:

1. **Result binds the ledger.** The result string gains a 5th field, an
   `esrCommitDigest` = sha256 of the securelock's signed authorization ledger
   (`esr.authorizations.json`), all-zero when the order made no ESR commits. The
   trustedzone puts it in the signed result; the validator recomputes it. A node
   that rewrites the ledger changes the digest and fails against the signed
   result. **The result version bumps `v3` → `v4`** to mark the new field.

2. **Each entry's signature.** For every ledger entry
   `{enclave, keyHash, cid, expectedVersion, relayNonce, signature}` the
   validator recomputes `commitDigest(...)` and confirms `recover(signature)` ==
   `enclave`. The node cannot fabricate an entry — it has no enclave key. So the
   digest, matched, means the result committed to a set of genuinely
   enclave-signed authorizations.

3. **On-chain cross-check.** For each entry the validator calls
   `getState(entry.enclave, entry.keyHash)` on the ESR contract and requires the
   on-chain **version ≥ expectedVersion + 1** — proof the commit actually landed
   (a node that pocketed the delegation without relaying leaves the chain
   unadvanced and fails here). `≥` rather than `==` so a later task re-committing
   the same key does not fail an honest validation.

**Where the validator looks — all from sealed state, nothing from the node:**

| What it needs | Source |
|---|---|
| ESR contract address | `ESR_CONTRACT_ADDRESS` from the injected `.env` (the sealed address the securelock/trustedzone used) |
| web3 provider | `ETNY_WEB3_PROVIDER` from the same `.env` |
| enclave / key / version / cid to look up | the signed ledger entry (bound by the signature) |

The node supplies none of the lookup coordinates and cannot point the validator
at a different registry, so it cannot make a forged ledger pass.

## Chain-fee correctness

Commit transactions must be built per-chain: **Bloxberg is legacy-gas**
(EIP-1559 methods return -32601), **LitVM is EIP-1559** (a legacy gasPrice below
base fee is rejected). The commit builder detects the chain's fee model
(eth_gasPrice as legacy; base-fee + priority as 1559) rather than assuming one.

## Status

- [x] `commitFor` contract — deployed both chains, security-proven live
- [x] `commitForBatch` in the contract (pox-smart-contract-litvm
      `EnclaveStateRegistry.sol`, this directory's copy,
      `test/EnclaveStateRegistry.test.js`)
- [ ] `commitForBatch` registry deployed per network with PoX 1.2
      (`deploy/08-deploy-esr.js`; compiled for paris, as every contract
      deployed on bloxberg is); the addresses in `commands/enums.py`
- [ ] node: bundle an order's commits with `commitForBatch` above the cap,
      drop the key `BatchEntryFailed.index` names and resubmit the rest,
      resend with more gas on `BatchEntryOutOfGas`
- [x] securelock: pure sign + stage per-nonce auth + order-wide ledger (no gas math)
- [x] trustedzone: independent re-price over the whole ledger, terminate on breach
- [~] node: relay staged auths + pay + running total + refuse over-budget
- [x] `ESR_GAS_LIMIT_EXCEEDED` (34) in TaskStatus across enclave copies
- [ ] runner: surface code 34
- [ ] wire extended-ESR addresses + release SDK + rebuild enclaves
