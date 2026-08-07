# Implementation Plan — Managed Enclave State Registry (ESR) + Unattended CLI

**Source:** ESR_Managed_Proposal.pdf (RFC, 2026-08-06, StarChart reference project)
**Repos:** `ethernity-cloud-sdk-py` (primary), `ethernity-cloud-sdk-js` (parity),
`ethernity-cloud-runner-py` / `@ethernity-cloud/runner` (client helpers),
`pox-smart-contract` (reference ESR contract), `etny-pynithy` (PUBLIC_KEY mode change)
**Status:** plan — reviewed against the code, not yet implemented

---

## 1. Review verdict

The proposal is sound and all four problems are **verified in the codebase**:

| # | Claim | Verified against |
|---|---|---|
| P1 | required enclave env silently empty | `Dockerfile.tpl:41-47` bakes exactly `SECURELOCK_SESSION, BUCKET_NAME, SMART_CONTRACT_ADDRESS, IMAGE_REGISTRY_ADDRESS, RPC_URL, CHAIN_ID, TRUSTED_ZONE_IMAGE` — nothing user-defined; `os.getenv("ESR_ADDRESS","")` inside the enclave is `""` by construction |
| P2 | testnet identity key is public | `securelock.py.tmpl` self-sign path derives the identity key deterministically from `mr_signer+mr_enclave` (both public); only mainnet CAS provisions a secret |
| P3 | curve mismatch | enclave certs are P-384 (secp384r1) across the stack; Ethereum requires secp256k1 |
| P4 | no unattended publish | `publish.py:142,153` `getpass` password prompts fire even with `ECLD_NON_INTERACTIVE` set (which exists at `publish.py:31-35` but only gates *confirmations*); no `ECLD_PRIVATE_KEY` |

### Correction 1 (design-blocking): mainnet address extraction cannot happen in the extraction service

§5.2 proposes the extraction service apply the §5.1 transform to emit `ESR_WALLET_ADDRESS`.
The transform requires the **identity private key** (`keccak(DOMAIN_SEP ‖ identity_priv_der)`).
On testnet that key is publicly derivable, so the service *can* compute it — but on
**mainnet the identity key is CAS-provisioned and exists only inside the enclave**. The
service holds only the public key and can never derive the wallet.

**Fix:** the **enclave emits its own wallet address**. Extend `ETNY_MODE=PUBLIC_KEY`
output (the existing cert-harvest channel that publish already parses, locally and via
the remote extraction service) to also print `ESR_WALLET_ADDRESS: 0x…` — computed
inside the enclave from whatever identity key it actually has. Works identically on
testnet and mainnet, keeps the private key inside, and reuses existing plumbing.

### Correction 2 (scope guard): Path 2 (secp256k1 cert) is more invasive than "cert-flow changes"

The enclave-to-enclave handshake is ECIES over **P-384** (`etny_crypto.*`), and the
on-chain Image Registry stores P-384 certs that trustedzone/securelock encrypt against.
Switching the cert curve breaks the wire protocol between enclaves, not just the cert
flow. Correctly listed as out of scope — it should stay there until a protocol-version
bump is planned.

### Already shipped (overlaps to reconcile, all since 2026-08-05)

- **`ecld-test` exists** (sdk-py ≥ 0.3.27, sdk-js ≥ 1.3.0): one-shot local execution
  with the enclave's own executor, plus `ecld-test serve` (local API) and **LOCAL mode
  in both runners** (runner-py ≥ 0.3.8, runner-js ≥ 0.3.6). §9's `ecld-test` table
  becomes an *extension* of this command (env vars, `--expect`, network mode), not a
  new command. It also already exits non-zero on failure (CI-usable today).
- **`IMPORT_ERROR` (28)** (sdk-py ≥ 0.3.29): backend import failures now surface
  eagerly with cause — the sibling of P1's silent-empty class. P1's fix should follow
  the same pattern (see `CONFIG_ERROR`, Phase 1).
- **Build-time backend gate** (0.3.29): missing/unparseable `backend.py` fails
  `ecld-build`. P1's fail-fast extends this existing check-site.
- `ECLD_NON_INTERACTIVE` / `ECLD_ASSUME_YES` / `ECLD_MEMORY_TO_ALLOCATE` partially
  honored (build + publish confirmations) — §9 completes them.

---

## 2. Answers to the RFC's open questions (§12)

1. **Extraction service emitting the address?** No — see Correction 1; the enclave
   emits it in PUBLIC_KEY mode, the service just relays output it already relays.
2. **Sanctioned identity-key exposure:** never expose the raw key to user code. Ship
   `StateRegistry` as SDK-vendored code inside the securelock image (next to
   `etny_exec.py`), which loads the key from the enclave's own path and exposes
   *operations* (sign/encrypt/commit) plus `wallet_address` — a signing handle, not
   key material.
3. **Canonical vs BYO contract:** v1 = reference contract shipped in the SDK
   (`contracts/esr/`), `ecld-init` offers `deploy new` / `enter address`. Canonical
   per-network addresses once the ABI has survived real use (v2).
4. **Path 2:** parked (Correction 2).
5. **Namespace:** `ecld.state` in-enclave (`from ecld.state import StateRegistry`).
   `ethernity.*` squats a top-level name the pip package doesn't own.

---

## 3. Phases

Ordered exactly as the RFC's rollout (§11), which is the right order: correctness
first, CI second, crypto third, money last.

### Phase 1 — ESR config schema + build-time injection + fail-fast (fixes P1)
**Repo: sdk-py** · est. ~1 day

- `commands/config.py`: typed `esr` block in `.config.json`:
  `{enabled, contract_address, wallet_address, autofund: {enabled, amount, threshold, max}}`.
  Additive; absent block ⇒ exactly today's behavior.
- `commands/pynithy/build.py` (extend the existing backend gate in
  `copy_backend_to_build_dir`): when `esr.enabled` and `contract_address` unresolved →
  **fail the build** with the same message style as the backend gate.
- `Dockerfile.tpl`: add `ENV ESR_CONTRACT_ADDRESS=__ESR_CONTRACT_ADDRESS__` (and
  `ESR_WALLET_ADDRESS` once known) — rendered **only when ESR is enabled** so non-ESR
  images keep an unchanged layer content.
- **`CONFIG_ERROR = 32`** in the extended enum (securelock + trustedzone + both runner
  decoders): if an ESR-enabled enclave boots with an empty required value, the task
  returns 32 with the missing variable named — same pattern as `IMPORT_ERROR` (28),
  closing the "valid PoX, empty result" hole for anything that slips past the build gate.

### Phase 2 — complete the unattended CLI (fixes P4)
**Repo: sdk-py** (js parity in Phase 6) · est. ~1–1.5 days

- Resolution precedence everywhere: **flag → env → `.config.json` → prompt** (prompt
  only if TTY and not `--yes`).
- `commands/private_key.py` + `publish.py`: **`ECLD_PRIVATE_KEY`** (and
  `ECLD_KEY_PASSWORD` for the encrypted-key path) replace the two `getpass` calls;
  in non-interactive mode a missing key is a hard error, never a hang. This retires
  StarChart's `run_publish.py`.
- `commands/init.py`: env/flag for every prompt (`ECLD_PROJECT_NAME`,
  `ECLD_DAPP_TYPE`, `ECLD_BLOCKCHAIN_NETWORK`, IPFS choice, template choice, the ESR
  prompts from §8) + the Custom-type docker prompts (`init.py:183-186` getpass).
- `commands/test.py`: `ECLD_TEST_CODE`, `ECLD_TEST_INPUT`, **`--expect` /
  `ECLD_TEST_EXPECT`** (exit non-zero on mismatch). Network mode (submitting a real
  testnet task via runner-py with `ECLD_RUNNER_KEY` / `ECLD_TEST_NETWORK`) rides on
  the runner already being scriptable — thin glue.
- `ECLD_REMOTE_CERT_EXTRACTION`, `ECLD_IPFS_TOKEN` env equivalents in publish.
- CI proof: a GitLab job in the SDK repo running init→build→test→publish (testnet)
  fully unattended — the acceptance test for this phase.

### Phase 3 — enclave identity wallet + address emission (fixes P2/P3)
**Repos: sdk-py (vendored securelock), etny-pynithy (stock securelock, optional), remote extraction service** · est. ~2 days + security review

- Vendored `securelock.py.tmpl`: when ESR enabled, derive
  `eth_priv = keccak256(b"ethernity-cloud/esr-wallet/v1" ‖ identity_priv_der)` →
  secp256k1 wallet (needs `eth-keys` + `pycryptodome` in the securelock image deps).
- **PUBLIC_KEY mode prints `ESR_WALLET_ADDRESS: 0x…`** next to the cert (Correction 1).
- `commands/pynithy/publish.py`: parse the new line from the container stdout /
  remote-service response; persist `esr.wallet_address` to `.config.json`.
- **Loud testnet warning** at build, publish, and enclave boot when the identity is
  self-signed: "identity is not secret on this network — do not fund with real value".
- Security review checklist: DOMAIN_SEP fixed and versioned; derivation only when ESR
  enabled; no code path serializes `eth_priv`; wallet address logged, key never.

### Phase 4 — auto-funding at publish (OPT-IN CONVENIENCE ONLY)
**Repo: sdk-py** · est. ~1 day

**Scope decision:** manual funding by the data owner is the DEFAULT and documented
path. The enclave emits its address; the data owner funds it as needed, because the
payload uses that address for its own purposes and only the data owner knows what it
needs. Auto-funding stays in the plan strictly as an opt-in convenience — never a
default, never implicit.

This narrows the risk the RFC worried about: money is moved because someone
deliberately turned it on, not as a side effect of publishing.

- `publish.py`, after address extraction: balance check → top-up transfer from the
  developer wallet (the key that already pays registration) with confirmation wait +
  tx-hash log. **Only when `esr.autofund.enabled` is explicitly true.**
- Guardrails exactly per §5.3: `esr.autofund.max` hard ceiling; interactive =
  confirm; unattended = amount must be explicitly set (no default transfer);
  idempotent top-up (`threshold`).
- Default off ⇒ publish only PRINTS the address and how to fund it; it never
  moves value on its own.
- Reuses the web3 wiring `publish.py`/`image_registry.py` already have per network.

### Phase 5 — `StateRegistry` in-enclave API + client helpers + reference contract
**Repos: sdk-py, runner-py, runner-js, pox-smart-contract** · est. ~3–4 days

- Reference ESR contract (`pox-smart-contract/EsrRegistry.sol` + ABI): keyed state
  CIDs with `expectedVersion` optimistic concurrency, event per commit. Seeded from
  StarChart's working flow. Deployed by `ecld-init --esr-contract deploy` on testnets.
- In-enclave module vendored into securelock src (`ecld/state.py`):
  `StateRegistry().get(key)` / `.commit(key, mutate_fn)` / `.wallet_address` —
  encryption key derived from the identity with a **second** domain separator
  (`…/esr-encryption/v1`), signing via the Phase-3 wallet, version conflict retry.
- Client helpers: `ethernity_cloud_runner_py.state` + a matching JS export in
  `@ethernity-cloud/runner` — read/decrypt path sharing the ABI + address from one
  source (kills StarChart's duplicated ABI in `index.html`).
- `ecld-test` parity: the local API gains an in-memory ESR stub so `StateRegistry`
  code paths run locally (get/commit against a dict, versioning honored) — keeps the
  local-first developer loop we just shipped intact.
- Migration per §10: deprecation warning when a backend derives a wallet from
  `MR_ENCLAVE` (grep-able pattern at build time, one minor version window).

### Phase 6 — Nodenithy/JS parity
**Repo: sdk-js** · est. ~2 days, after 1–5 stabilize on Pynithy

Same schema, same env matrix, same PUBLIC_KEY-mode emission from the JS securelock,
`ecld.state`-equivalent module for `backend.js`.

**Status: done except the `ecld.state` module** (which is the JS half of Phase 5
and correctly waits for Phase 5 to land on Pynithy first — there is no reference
implementation to mirror yet). Shipped, adapted to this SDK's `.env`-based
project config rather than `.config.json`:

- `esr_wallet.js` — same derivation as `esr_wallet.py`, binding to the identity
  key as held (see security-review Q1). Verified byte-exact against Python.
- securelock emits `ESR_WALLET_ADDRESS` in PUBLIC_KEY mode, reading the key from
  `key_file` — the only source that is correct on *both* paths (CAS provisions it
  there on mainnet, the testnet self-sign writes it there).
- `ecld-init` ESR step + `ECLD_ESR_ENABLE` / `ECLD_ESR_CONTRACT` env overrides.
- `build.mjs` fail-fast gate + `__ESR_ENV__` rendered only when enabled, so
  non-ESR images keep their previous layer content.
- `CONFIG_ERROR = 32` (Phase 1's enclave half) in `task_status.js` +
  `etny_exec.js`, synced across the three runtime nodenithy enclave trees.

The same emission was added to the **stock runtime nodenithy** securelock, so
non-SDK enclaves emit the address too. Takes effect on the next enclave build
(which changes MRENCLAVE and needs re-registration, as any enclave change does).

### Extraction service (certex) — relays the address

`template-export-public-key` now relays the enclave-emitted line so the address
reaches both SDKs (see security-review Q4, which this resolves). Backward
compatibility verified against the published SDKs; the workers run the schema
migration themselves so deploy order does not matter. **DEPLOYED and live.**

Deploying it surfaced that the extractor host carried ~350 lines of uncommitted
hardening (IPFS-tar validation, log-polling cert harvest, input sanitisation)
that existed nowhere in git — a `git pull` would have destroyed it. That work was
committed first, the ESR relay was then rewritten against the live code (the
certificate now comes from a `docker logs` poll whose `$LOGS` is scoped to the
retry loop), and `main` is now byte-identical to the running host. The live
database was also untracked (`.gitignore`) since its permanent-modified status is
what hid the drift; every process now bootstraps the schema so a fresh checkout
works regardless of start order.

Verified on the live system: both units active, the `esr_wallet_address` column
migrated on the production database at startup, and a real pre-existing hash
still returns its certificate with no extra field.

---

## 4. Sequencing & effort summary

| Phase | Deliverable | Est. | Depends on | Status |
|---|---|---|---|---|
| 1 | ESR schema, build gate, `CONFIG_ERROR` 32 | 1d | — | done (py + js) |
| 2 | full unattended CLI + CI acceptance job | 1.5d | — (parallel with 1) | done (py) |
| 3 | identity wallet + enclave-emitted address | 2d + review | 1 | implemented; **awaiting review sign-off** |
| 4 | auto-funding | 1d | 3 | **not started — gated on the Phase 3 review** |
| 5 | StateRegistry + contract + client helpers | 3–4d | 3 (4 for funding tests) | not started (no reference contract yet) |
| 6 | JS/Nodenithy parity | 2d | 1–5 | done except `ecld.state` (waits on 5) |
| — | certex relays the address | — | 3 | **deployed and live** |

~9–11 engineering days total. Phases 1+2 are independent, immediately valuable, and
risk-free (no crypto, no money) — ship them as the next SDK minor (0.4.0: the ESR
schema is the first structural `.config.json` change). Phase 3 is the security-review
gate; nothing after it ships until that review passes.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Enclave deps grow (`eth-keys`, `pycryptodome`) → image/MRENCLAVE churn | deps land in the base requirements once, in the same release as Phase 3; MRENCLAVE changes on any SDK upgrade anyway |
| Auto-funding from CI = money moved by a bot | unattended requires explicit amount + hard ceiling + top-up-only; log tx hash; Phase 4 separable if unwanted |
| Testnet-public wallet funded by habit | tri-point loud warning (build/publish/boot) + docs; fund-minimum guidance in init defaults |
| Remote extraction service availability (already a publish dependency) | unchanged dependency; enclave-emitted address adds no new coupling |
| `expectedVersion` races under concurrent tasks | optimistic-retry in `StateRegistry.commit`; document last-writer-wins boundaries |

## 6. First concrete step

Phase 1 + 2 in one branch (`feat/esr-phase1-2`) on `ethernity-cloud-sdk-py`:
config schema, build gate, `CONFIG_ERROR` 32 across the four repos' enums, the
`ECLD_PRIVATE_KEY` publish path, init env matrix, `ecld-test --expect`, and the
unattended CI acceptance job. Everything there is testable with `ecld-test` locally
and one testnet publish.
