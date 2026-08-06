# SGX key-gen module (`get_sgx_report.so`)

The SDK ships this module **only as a prebuilt `.so`** — never the `.c` source.
The identity-key derivation (SGX EREPORT + obfuscated MR_SIGNER + HKDF-SHA512
domain-separated derivation) lives entirely in the compiled binary, so a
developer inspecting the wheel or the build tree never sees the algorithm.

## Where the source lives

The source of truth for `get_sgx_report.c` is the runtime repos, **not** this
package:

- `etny-pynithy`   → `v3/build/{securelock,trustedzone}/src/get_sgx_report.c`
- `etny-nodenithy` → `v3/build/{securelock,trustedzone,validator}/src/get_sgx_report.c`

All copies are byte-identical.

## How the `.so` is produced

`get_sgx_report.so` is a **build artifact of the pynithy / nodenithy pipelines**.
Their Dockerfiles already cross-compile `get_sgx_report.c` with `scone-gcc`
(SCONE/musl ABI — required to load inside the enclave). To refresh the copy the
SDK ships, take the `.so` those pipelines produce and commit it here as
`src/get_sgx_report.so`.

`scripts/get_keygen_so.sh` copies the `.so` from a local pynithy checkout (or
build output) into the SDK; run it after the pynithy build produces a fresh
`.so`, then commit `src/get_sgx_report.so`.

## Why not compile it in the SDK build

The developer's `ecld-build` must not compile the source, or the source would
have to ship. By consuming a prebuilt `.so`, the SDK carries no source at all.
`build.py` fails fast if `src/get_sgx_report.so` is missing.

## Honest scope

This hides the SOURCE, not the algorithm: the `.so` still contains the
executable logic and the enclave image is public (IPFS + on-chain MRENCLAVE).
On testnet there is no attestation, so the derived key is reproducible by anyone
who runs the image. Key exclusivity comes from attestation (mainnet/CAS), not
from shipping only the binary. See `../src/esr_wallet.py`.
