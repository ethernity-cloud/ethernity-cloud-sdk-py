# Tests

## `spec/` — the shared fixtures, byte for byte

`tests/spec/` holds copies of `pox-smart-contract-litvm/spec/*.json` and
`spec/metadata-keys.md` with that directory's `MANIFEST.sha256` (the
`sha256sum` text format). The copies are made with a plain file copy, never
re-serialised, and `.gitattributes` exempts them from line-ending conversion.
`test_spec_manifest.py` hashes every copy against the manifest and, when a
`pox-smart-contract-litvm` checkout sits beside this repository, compares the
bytes with the source. Regenerate the source fixtures there (`spec/make/`,
`manifest.js` last), then copy the changed files and the manifest here.

## `e2e_testnet_cas.sh` — the CAS-attested testnet, end to end

Builds a securelock with this SDK, publishes it against the ethernity-cas
validator set (session registered on-chain, certificate from a CAS session),
runs one task on the network and waits for a CAS validator's verdict on the
order. Needs an SGX host with docker, a `registry.scontain.com` login, the
SDK and `ethernity-cloud-runner-py` installed, and a funded testnet wallet in
`ECLD_PRIVATE_KEY`. Spends testnet gas and tETNY.

```bash
ECLD_PRIVATE_KEY=0x... bash tests/e2e_testnet_cas.sh
```

## `unattended_acceptance.sh` — release gate

Proves the CLI is fully drivable without a terminal: every command either
succeeds or fails with an actionable error, and **never hangs on a prompt**.
That failure mode is what blocks CI/CD pipelines using the SDK (it is why the
StarChart project needed a custom `run_publish.py` wrapper), so it is worth
enforcing on every release.

What it checks:

1. `ecld-init` driven entirely by `ECLD_*` env vars, including the opt-in ESR
   step — then asserts `.config.json` (the ESR block, the complete default
   `EXECUTION` block) and the scaffolded backend.
2. The ESR build gate fails fast on an unresolved registry address.
3. `ecld-test`: `--expect` match, env-driven run, non-zero exit on mismatch,
   non-zero exit on task failure, and `CONFIG_ERROR` (32) when a required
   enclave value is empty.
4. `ecld-publish` errors actionably (naming `ECLD_PRIVATE_KEY`) instead of
   prompting when no wallet is configured.
5. The ESR identity wallet derivation and address capture.
6. The publish summary of the relay model: the identity address, no funding.
7. The `EXEC_*` export `ecld-publish` writes to `src/.env`, through
   `write_env` directly: the ten variables of `execution.ENV_KEYS` in order,
   reading back as the block they came from.

Requirements: **bash + python3 + the SDK's dependencies**. No docker daemon,
no network, no wallet, no SGX — nothing is built or published.

### Run it

From a checkout with the SDK installed (`pip install -e .`):

```bash
bash tests/unattended_acceptance.sh
```

In a container, on any runner (installs deps itself):

```bash
docker run --rm -v "$PWD":/sdk -w /sdk python:3.11-slim \
  bash -c "pip install -q -e . && bash tests/unattended_acceptance.sh"
```

On Windows, a bare `bash` often resolves to **WSL** — a separate Linux
environment that cannot run your venv's `python.exe`. Use Git Bash and pin the
interpreter:

```bat
for /f "delims=" %P in ('py -c "import sys; print(sys.executable)"') do set "ECLD_TEST_PYTHON=%P"
"%ProgramFiles%\Git\bin\bash.exe" tests/unattended_acceptance.sh
```

### Wire it into a release

The suite exits non-zero on the first failure, so gate the build on it. In the
local `build.bat` (untracked — `*.bat` is gitignored), the call sits before
`py -m build`, which keeps a regression out of `dist/` and therefore out of
`twine upload`. Any CI system can do the same with the docker one-liner above.

## The securelock's session runtime (`test_f_*.py`)

`f_session_support.py` loads the securelock sources afresh and stands in for
the securelock app (the trustedzone objects it verifies, the objects it
pushes) over esr_local's MemSwift. `test_f_securelock_probe.py` reproduces
`heartbeat.json`'s beats and ready plaintext from `securelock_probe.py` and
runs the heartbeat thread (the ready object before any beat, the busy edges,
the idle tail, a tick overrun by a put skipped). `test_f_securelock_boot.py`
reads the template's wiring from `securelock.py.tmpl` (the boot order, the
transfer exit, the tail after a reported failure, a refused put raising, the
signed prefix of a verified object). `test_f_session_commits.py` signs with
`session-commits.json`'s ESR wallet over the real `commitDigest` and checks
the step's `session.commit.<n>.<key16>.<relayNonce>.json` files, the output
envelope and `esr.authorizations.json` byte for byte, the fence's file kept
as `esr.commit.*.json`, and an `ecld-test` run unchanged.
`test_f_session_transfer.py` stages the `transfer` and `close` controls
around a blocking handler, the idle tail decoded after the cut.
`test_f_ecld_state_fence.py` places commits on
esr_local's chain around `session-fence.json`'s bound and runs the fence,
with the signal verified under `capability-record.json`'s trustedzone key.
`test_f_fence_window_expiry.py` runs the fence wait's window: a reserved
order (`session.config` `reserved` true) ends with a `SYSTEM_ERROR` result
when no fence arrives within `READY_WINDOW_SECONDS`, an unreserved one
waits for nothing. `test_f_session_hold_exits.py` checks the boot commits
staged as `esr.commit.*.json` on every exit with a result before an output
and `_task_ledger` restored by `discard_step_commits`.
`test_f_probe_start_refused.py` compiles `start_probe` from the template:
a refused ready put leaves `self.probe` None.

## The build's image at the publish

`test_build_records_securelock.py` runs `build.record_securelock_image` with
the command runner replaced: `SECURELOCK_IMAGE_DIGEST` is the local
registry's RepoDigest of the pushed securelock and `MRENCLAVE_SECURELOCK_SIGNED`
the image's `/signed_mrenclave.txt`, and a build that can read neither
fails. `test_local_registry.py` runs `LocalRegistry` over a recorded
`subprocess.run`: the `ecld-registry-<project>` container over the tree,
mounted read-only and published on the loopback interface at a port free
there (`local_kubo.free_port`; the daemon under Docker Desktop does not reach
a host port it assigns itself), refused without a build tree, removed when
docker refuses the run, when it does not answer, and on `stop`, with the tags
pulled from it, and `stop` raising nothing without a docker binary.
`test_publish_build_gate.py` imports `publish.py` from a directory
holding a `.config.json` (the module builds an ImageRegistry from it at
import) and checks the local compose rendered against the registry's address
while the shipped one keeps `localhost:5000`, the gate's exact messages on a
tree serving another digest and on a measurement other than the recorded
signed MRENCLAVE, the pass line, the registry removed and the Kubo released
when `main` fails, is interrupted or completes and when the Kubo does not
start or is interrupted while starting, the SIGTERM/SIGHUP handler `main`
installs (`exit_on_interrupt`), the Kubo `serving` from the image's
registration until its certificate is on chain (left serving by a publish
that ends in between, serving nothing when the registration fails), and the
message a build by an older SDK ends with. `test_local_kubo.py` covers the
publish's Kubo over a recorded `subprocess.run`: its name, the address
registered on chain, the API port of a kept node read from `docker port`,
the takeover of the node a previous publish left (no `docker run`; a stopped
one started with `docker start` first), which serves from the takeover on,
the swarm port published when free, left unpublished when in use, and the run
made again without it, with the notice, when docker refuses the port as
already allocated, a run refused for another reason or twice leaving no
container, a node started afresh whose API does not come up removed and a
taken-over one left to `release`, `stop`, also without a docker binary,
`release` stopping the container unless it serves a registered image, when it
is kept with the notice naming it, and `exit_on_interrupt` making a SIGTERM
or SIGHUP raise KeyboardInterrupt.

## The execution object

`test_execution.py` runs `commands/execution.py` against the
`execution-spec.json` copy: the defaults and the schema rows, the three
normalization cases, the ten validation cases with their exact messages, the
`EXEC_*` parsing (`from_env`) and export (`to_env`), the refusal of an
`EXEC_*` or `ECLD_EXEC_*` variable outside `ENV_KEYS` (`check_env`), and the
relays-per-hour default. `test_init_execution.py` drives `ecld-init` as
`unattended_acceptance.sh` does and asserts the complete `EXECUTION` block it
writes, the `ECLD_EXEC_*` parsing and the message on a bad value, an unknown
stored key or an unknown `ECLD_EXEC_*` variable;
`test_run_task.py` covers `ecld-run`'s fractional `--task-price`, the
execution flags over the `EXEC_*` variables over the `.config.json` block,
the usage error of an unknown option or `EXEC_*` variable, and the nested
`execution` the runner receives beside the flat `duration`.
`test_publish_execution.py` covers the publish's gate on the stored block
and `write_env` leaving exactly the `ENV_KEYS` variables in `src/.env`.

## The fee per gas and its ceiling

`test_fee_ceiling.py` covers `commands/fees.py` through its three users,
with web3 replaced. The default ceiling and the priority fee of every
EIP-1559 network of the table are held by value. At the image registry's
transaction options (`ImageRegistry._transaction_options`, the module
imported from a directory holding a `.config.json`) and at the
SessionRegistry's fee fields (`session_registry.fee_fields`), each case on
both: Polygon mainnet's default ceiling of 500 gwei (a base fee of 425 gwei
gives `maxFeePerGas` 499.5 gwei, 426 is refused after one read of the block,
with one text for both sends; a fee equal to the ceiling is sent and a fee
one wei above it is refused);
`ECLD_MAX_FEE_PER_GAS` as the ceiling, above and below Polygon mainnet's
default and on every EIP-1559 network of the table; an empty value leaving
the default; a value that is not a positive whole number in up to 30 digits
alone (a number with spaces around it, one of 31 digits and one of 5000,
more than `int()` converts by default, included) refused with the variable
and the value named, before the block is read, and one of 30 digits taken as
the ceiling; a network of legacy transactions (bloxberg) taking its gas
price whatever the variable holds.
An `addImage` transaction is not built and `ImageRegistry._send` raises
above the ceiling, with nothing signed.
`ImageRegistry.register_securelock_image`, the registration on a registry
that takes an image by `addImage`, raises the refusal after one read of the
block, with nothing built and no second attempt; at a base fee the ceiling
admits it reads the block before each of its two transactions and again as
it builds each, and on bloxberg it reads none. `ecld-publish`'s main
(`commands/publish.py`) runs in-process from the key to the publish script,
its image registry the real one over the stand-in chain, the ownership check
and the publish script (which posts the session, pins the image and sends
the registration) replaced by recorders and every request of `requests`
recorded: above the ceiling (on Polygon mainnet, and on every EIP-1559
network at a base fee equal to its default ceiling), and on a variable it
cannot read, it stops at the step after the gas balance with the refusal
printed and exit code 1, having reached neither recorder, made no request
and signed nothing; at a base fee the ceiling admits it goes on to the
publish script; on bloxberg the step does not run.
`test_session_registry_fees.py` holds the SessionRegistry's fees at the
networks' default ceilings.
