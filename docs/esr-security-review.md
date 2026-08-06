# ESR Phase 3 — Security Review Notes

Companion to `docs/esr-implementation-plan.md` §Phase 3. This is the material a
reviewer needs to sign off the enclave identity wallet before it is used with
real value. **Status: implemented, awaiting review.**

## What was implemented

`build/securelock/src/esr_wallet.py` (vendored into the enclave image):

```
eth_priv    = keccak256("ethernity-cloud/esr-wallet/v1" || identity_priv_der)
eth_address = address(secp256k1(eth_priv))
```

`securelock.py.tmpl` emits `ESR_WALLET_ADDRESS: 0x…` on the same stdout channel
as `PUBLIC_CERT`, immediately after the cert and before any SwiftStream access
— only when `ESR_CONTRACT_ADDRESS` is baked in (i.e. the project enabled ESR).
`publish.py` captures it into `.config.json` under `ESR.wallet_address`.

## Invariants (verified by `tests/unattended_acceptance.sh` step 5)

| Invariant | How it is enforced |
|---|---|
| Deterministic per identity | pure function of the identity key; same key → same address |
| Bound to the enclave identity | different identity key → different address |
| Domain-separated | `DOMAIN_SEP` prefix; the wallet key is provably not a plain hash of the identity key, so it cannot collide with any other use of that key |
| Valid secp256k1 scalar | `0 < k < N`, with re-hash on the (astronomically unlikely) invalid draw |
| Address matches the key | cross-checked against `eth_account.Account.from_key` |
| **Private key never leaves** | nothing in the module returns, logs or serializes the key; the enclave prints only the address; `publish.py` parses only a `0x`-address regex |
| Opt-in | derivation runs only when `ESR_CONTRACT_ADDRESS` is present in the image |
| Fail-soft | derivation errors print `ESR_WALLET_ERROR` and never break task execution |

## Security posture by network — the load-bearing distinction

The question ESR depends on is narrow, and it is **not** "is the enclave
genuine". SGX protects enclave memory on every network. The only question that
decides whether the wallet is safe to fund is: **can anyone outside the enclave
reproduce the private key?**

**Mainnet (decided): SGX attestation-derived key.** CAS verifies the DCAP quote
and provisions the identity key over the attested channel. It exists only
inside genuine enclaves of this MRENCLAVE and is identical across nodes, so the
derived wallet is enclave-only, stable and unforgeable. Intended posture; no
further work.

**Testnet (in progress): port the trustedzone key generator into securelock.**
Decided direction: securelock should use the same in-enclave key-generation
library the runtime trustedzone uses, shipped as a **compiled module** (like the
existing `get_sgx_report.so`), rather than the current
`MR_SIGNER+MR_ENCLAVE` seed. That library is not in this repo yet — the
SDK-vendored securelock still self-signs from public measurements
(`generate_cert_from_mrenclave`), and so does `etny-pynithy`'s trustedzone on
`feature/v3-multinetwork` (line 824 → line 160), which logs
"TESTNET MODE: Using pre-released SGX - NOT SECURE FOR PRODUCTION".

⚠️ **Compilation and obfuscation do not create key secrecy.** The enclave image
is published on IPFS and its MRENCLAVE is on-chain, so anyone can run the code
and observe what it derives. Secrecy requires a secret *input* that never
leaves the CPU — an `EGETKEY`/sealing key (per-platform, so it would differ per
node, which has its own consequences for a wallet expected to be stable), or a
secret injected at provision time and kept out of the published image. Hiding
the algorithm raises effort; it does not change reproducibility.

**Therefore the warning stays until the ported generator is shown to mix a real
secret**, and it is now switchable by configuration rather than hard-coded:

1. the enclave logs `[TESTNET-INSECURE]` next to the address,
2. `publish.py` warns when it captures an address from such output,
3. `esr_wallet.is_secret_identity(network_type)` is the programmatic check for
   any path that must refuse or restrict (e.g. Phase 4 auto-funding),
4. **`ESR_IDENTITY_SECRET=1`** baked at build time makes (1)–(3) treat the
   identity as secret — the switch to flip when the ported generator lands and
   its secret input has been reviewed. No code change required then.

## Next work item — port the testnet key generator into securelock

Decided, not yet implementable here: the generator library lives outside this
repo (not on any `etny-pynithy` branch searched). To land it:

1. Locate the library the runtime trustedzone uses for testnet identity keys.
2. Determine its secret input (`EGETKEY`/sealing key, provisioned secret, or
   none). This single fact decides everything below.
3. Cross-compile it in the binary-fs stage next to `get_sgx_report.so` and load
   it from `securelock.py.tmpl`'s testnet branch in place of
   `generate_cert_from_mrenclave()`.
4. Keep trustedzone and securelock on the **same** generator — divergent
   identity schemes between the two enclaves is a bug class of its own.
5. If (2) yields a real secret: bake `ESR_IDENTITY_SECRET=1` and the warnings
   disappear on their own. If it does not: ship it anyway for parity, keep the
   warning, and treat testnet ESR wallets as disposable.

⚠️ If the secret turns out to be an `EGETKEY`-derived sealing key, note that it
is **per-platform**: the same enclave on two different nodes would derive two
different wallets. That is fine for node-local state but breaks the "one wallet
per enclave identity" model ESR assumes — worth settling before the port.

## Reviewer questions to settle before Phase 4 (auto-funding)

1. **Is keccak-of-the-DER the right binding?** The DER encoding of the identity
   key is stable for a given key, but it is an encoding, not a canonical scalar.
   If CAS ever re-encodes the same key differently the address would change.
   Consider binding to the raw private scalar instead — cheap to change now,
   breaking later (funds sit at the old address).
2. **Address-change handling.** A new MRENCLAVE (any SDK upgrade or code change
   on testnet) yields a new identity and therefore a new address. `publish.py`
   warns; should it also refuse to proceed if the old address holds a balance?
3. **Auto-funding on testnet.** Given the key is public, Phase 4 should
   probably refuse to fund above a hard-coded dust ceiling when
   `is_secret_identity()` is False, rather than relying on the configured
   `autofund.max`.
4. **Remote extraction gap.** On mainnet the address can only come from a local
   (SGX) extraction, because the identity key never leaves the enclave. The
   extraction service would have to relay the enclave's own emitted line (an
   `esrWalletAddress` field is already consumed if present). Until then,
   ESR + mainnet + no local SGX = no address; publish says so explicitly.
5. **Key-material audit.** Confirm no logging path can serialize
   `private_key_pem` alongside the ESR code (the enclave already truncates the
   key file after load on mainnet).

## Out of scope (unchanged)

Path 2 (switching the enclave cert to secp256k1) remains parked: the
enclave-to-enclave handshake is ECIES over P-384 and the on-chain Image
Registry stores P-384 certs, so changing the curve breaks the wire protocol
between enclaves, not just the cert flow.
