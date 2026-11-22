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

## The securelock's store credential

`test_store_credential.py` holds `src/store_credential.py` to the reader's
text by its SHA-256 and its length over LF line endings, and runs the reader
over its vectors (26 file contents, the files given as bytes, a directory,
nothing and a regular file as the parent at the path, the call with no
argument): the keys or the literal pair, the source, and the one log line,
which names the path and the reason and holds neither key and nothing of
the file. It reads the sources: the literal pair is in the reader alone,
`securelock.py.tmpl` calls `store_credential.load()` once, with no argument,
in the statement before the one that constructs its one `SwiftStreamService`
from the two keys, `scripts/binary-fs-build.sh` declares the host path
`/run/ecld` between `/etc/resolv.conf` and `/etc/hosts`, `Dockerfile.tpl`
holds `LABEL cloud.ethernity.securelock.store="file"` and then
`RUN mkdir -p -m 0700 /run/ecld` on the line before its `ENTRYPOINT`, and no
Dockerfile of the image names a `USER`. Every object
name the securelock passes to a put matches one of the nine patterns the
agent's policy lets a securelock's store user put, and it asks no removal:
over a plain task and a session run from the template, and over a plain
task's commit, a session step's commit and a fence under esr_local.
`ecld-build`, run with its docker commands recorded, hands docker a context
with the reader and the host path and a Dockerfile that ends with the label,
the directory and the `ENTRYPOINT`; the two composes of `ecld-publish` mount nothing at
`/run/ecld`. `test_f_log_confidentiality.py` runs the template's securelock
under no credential file, under a file the reader takes and under one it
refuses, and against a store on the loopback interface that refuses the
file's credential or ends every connection (through the template's own
`swift_stream_service` over minio; skipped without minio): the store client
is constructed once, after the reader's one line, from the file's keys or
the literal pair, neither key is written to stdout, stderr or the logging
module, the payload's globals, the environment and the bucket hold none, and
a store that refuses the file's credential is asked under no other.

The store tests of `test_store_credential.py` need a MinIO
(RELEASE.2023-03-13T19-46-17Z, the node agent's) and are skipped unless
`ECLD_TEST_STORE_ENDPOINT` names its `host:port`, with
`ECLD_TEST_STORE_BUCKET` (a bucket that holds `.env`),
`ECLD_TEST_STORE_ROLE` (`trustedzone` or `securelock`) and
`ECLD_TEST_STORE_CREDENTIAL` (the path of the `store.json` of a user the
node agent made for that bucket and role). Through the repository's
`SwiftStreamService`, built from the reader's answer for that file: the
bucket answers its head, an absent object answers `NoSuchKey`, a
trustedzone's user puts, reads and removes any object, a securelock's user
reads any object, puts every name its policy admits, removes nothing and
puts no other enclave's name in one request or in parts, an object of 6 MiB
is put in parts, and every call on `etny-bucket-integration` is refused.

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

## The pin on the public IPFS API

`test_ipfs_client_pin.py` runs `IPFSClient.pin`, which a publish on a
first-generation image registry asks of the public IPFS API, with
`requests.post` and the module's clock replaced. The bounds are held by
value: 5 attempts, 10 s between two, 30 s to connect and for a `pin/ls`,
120 s of silence, a byte at a read. The `pin/add` is asked with
`recursive=true&progress=true` under the client's `Authorization` header and
`Accept-Encoding: identity`, and read as a stream, its two timeouts cut to
the time the pin has left; the pin is true at the line that names the CID
among its `Pins`, after progress lines in the forms of Kubo 0.4.23 and of
later versions or at once, with nothing printed. The lines are put together
from the pieces the reads return (lines split and joined, a byte each), and
a last line without a line feed is read. An answer that ends without the pin
line, a read timeout or a dropped connection mid-stream, a request that gets
no answer, an error line and a status that is no refusal are each followed
by `pin/ls` under the `Authorization` header alone: a CID it lists is pinned
after one `pin/add`, and anything else (the API's "not pinned", another CID
listed, the CID listed under a 500, `Keys` that are a string holding the CID
or a number, a 503, no answer) is followed 10 s later by another `pin/add`,
with `the pin took 2 attempts` printed when that one reports the pin. Five
attempts without a pin are false with what the last one ended with printed
once: the answer's end (a line whose `Pins` do not name the CID, or are a
string holding it, an object keyed by it, a number or `true`, is not its
pin), the exception, the status and the first 300 characters of the body, or
the error line's `Message` (which may name the CID). A 401 and a 403 are
false after one request, at the first attempt and at a later one, with the
status and the body printed. The body of a status is read as far as its
first 300 characters, 1200 bytes of UTF-8, and left when the pin's time runs
out. Once the pin's time has passed no attempt starts and no `pin/ls` is
asked; an answer still arriving then, in lines or in bytes without a line
end, is left at the next piece a read returns, the line that piece completes
is still read, and a pin given no time makes no request. Under the publish's
spinner a pin is one line and a repeated pin one more. Three cases run over
HTTP against an `http.server` on the loopback interface: the pin read from a
chunked stream asked with `Accept-Encoding: identity`, a silent answer left
at the silence bound, before the server ends it, and settled by `pin/ls`,
and a pin line read when it arrives in an answer without chunks or a length
that stays open.

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
