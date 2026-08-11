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
relay lands under the enclave with the node paying.

  bloxberg testnet  0xF76469A5659670B6ade366dE635e6463aaB8f3D8  (legacy-gas)
  litvm liteforge   0xEF434486C0dbA37A9EaC8Ffe9A91190788D42054  (EIP-1559)

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

## Chain-fee correctness

Commit transactions must be built per-chain: **Bloxberg is legacy-gas**
(EIP-1559 methods return -32601), **LitVM is EIP-1559** (a legacy gasPrice below
base fee is rejected). The commit builder detects the chain's fee model
(eth_gasPrice as legacy; base-fee + priority as 1559) rather than assuming one.

## Status

- [x] `commitFor` contract — deployed both chains, security-proven live
- [x] securelock: pure sign + stage per-nonce auth + order-wide ledger (no gas math)
- [x] trustedzone: independent re-price over the whole ledger, terminate on breach
- [~] node: relay staged auths + pay + running total + refuse over-budget
- [x] `ESR_GAS_LIMIT_EXCEEDED` (34) in TaskStatus across enclave copies
- [ ] runner: surface code 34
- [ ] wire extended-ESR addresses + release SDK + rebuild enclaves
