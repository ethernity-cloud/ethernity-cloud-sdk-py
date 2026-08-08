# Tests

## `unattended_acceptance.sh` — release gate

Proves the CLI is fully drivable without a terminal: every command either
succeeds or fails with an actionable error, and **never hangs on a prompt**.
That failure mode is what blocks CI/CD pipelines using the SDK (it is why the
StarChart project needed a custom `run_publish.py` wrapper), so it is worth
enforcing on every release.

What it checks:

1. `ecld-init` driven entirely by `ECLD_*` env vars, including the opt-in ESR
   step — then asserts `.config.json` and the scaffolded backend.
2. The ESR build gate fails fast on an unresolved registry address.
3. `ecld-test`: `--expect` match, env-driven run, non-zero exit on mismatch,
   non-zero exit on task failure, and `CONFIG_ERROR` (32) when a required
   enclave value is empty.
4. `ecld-publish` errors actionably (naming `ECLD_PRIVATE_KEY`) instead of
   prompting when no wallet is configured.

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
