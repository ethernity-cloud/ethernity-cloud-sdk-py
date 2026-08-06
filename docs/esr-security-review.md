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

**Mainnet: intended posture.** The identity key is provisioned by CAS over the
attested channel, exists only inside genuine enclaves of this MRENCLAVE, and is
identical across nodes. The derived wallet inherits exactly those properties:
enclave-only, stable, unforgeable.

**Testnet: insecure by design.** With no CAS the identity key is derived from
`MR_SIGNER`/`MR_ENCLAVE`, both **public**. Therefore the ESR wallet's private
key is **reproducible by anyone** who can build the same enclave. State commits
signed by it are forgeable and any balance is drainable.

This is surfaced in three places (RFC §7):
1. the enclave logs `[TESTNET-INSECURE]` next to the address,
2. `publish.py` prints a warning when it captures an address from such output,
3. `esr_wallet.is_secret_identity(network_type)` is the programmatic check for
   any future code path (e.g. auto-funding) that must refuse or restrict.

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
