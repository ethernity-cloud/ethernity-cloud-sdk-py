"""ecld-test — run a payload locally, without SGX, exactly as the enclave would.

Executes the code string against the project's src/serverless/backend.py using
the SAME vendored executor (etny_exec.py) that ecld-build bakes into the
securelock enclave: same function reflection, same ___etny_result___ /
___etny_data_set___ globals, same expression auto-wrap, same TaskStatus codes.

This validates the CALL — function names, arguments, result wiring — before a
single on-chain request is paid for. It does not validate SGX attestation,
checksums, or encryption; those need a real (testnet) run.

Usage, from the project root (where .config.json lives):

    ecld-test 'hello("World")'                 # one-shot, in-process
    ecld-test --file payload.py
    ecld-test --input data.json 'process(___etny_data_set___)'
    ecld-test --input-text '{"x": 1}' 'process(___etny_data_set___)'

    ecld-test serve [--port 8745]              # local API for runner LOCAL mode

`serve` starts the local test API (local_api.py, part of the securelock
source) so a dApp can exercise its real runner integration end to end:
point the runner at LOCAL mode and every runner.run() executes against
this API instead of the blockchain.

Exit code 0 on TaskStatus SUCCESS, 1 otherwise.
"""

import argparse
import importlib.util
import os
import sys

# TaskStatus names as the runner reports them (frozen wire contract 0-8).
TASK_STATUS_NAMES = {
    0: "SUCCESS",
    1: "SYSTEM_ERROR",
    2: "KEY_ERROR",
    3: "SYNTAX_WARNING",
    4: "BASE_EXCEPTION",
    5: "PAYLOAD_NOT_DEFINED",
    6: "PAYLOAD_CHECKSUM_ERROR",
    7: "INPUT_CHECKSUM_ERROR",
    8: "EXECVE",
    28: "IMPORT_ERROR",
    32: "CONFIG_ERROR",
    33: "EXECUTION_TIMEOUT",
    34: "ESR_GAS_LIMIT_EXCEEDED",
    35: "SECURITY_VIOLATION",
    36: "ESR_NONCE_VIOLATION",
    37: "ESR_RELAY_TIMEOUT",
    38: "ESR_COMMIT_LIMIT_EXCEEDED",
}

VENDORED_EXEC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "commands", "pynithy", "build", "securelock", "src", "etny_exec.py",
)


def _load_enclave_executor(project_src):
    """Import the vendored etny_exec with the project's backend on sys.path.

    The vendored module does `import serverless.backend` at import time, so the
    project's src/ directory must be first on sys.path before loading it. Any
    previously imported serverless module is dropped so repeated calls (tests)
    pick up the right project.
    """
    for mod in ("serverless", "serverless.backend"):
        sys.modules.pop(mod, None)

    if project_src and os.path.isdir(project_src):
        sys.path.insert(0, project_src)

    # The vendored securelock src dir must be importable BY NAME so the backend's
    # `from ecld_state import StateRegistry`, and the local ESR emulator
    # (esr_local), resolve exactly as they do inside the enclave.
    vendored_dir = os.path.dirname(os.path.normpath(VENDORED_EXEC))
    if vendored_dir not in sys.path:
        sys.path.insert(0, vendored_dir)

    path = os.path.normpath(VENDORED_EXEC)
    if not os.path.isfile(path):
        raise RuntimeError(f"vendored enclave executor not found at {path}")
    spec = importlib.util.spec_from_file_location("_ecld_local_etny_exec", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# A well-known dev/test Ethereum address used when nothing else is configured
# (the first Hardhat/Anvil account -- recognizable and clearly not real funds).
DEFAULT_TEST_CALLER = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def _resolve_caller(explicit=None):
    """The task caller for local runs -- the DEVELOPER'S address, mirroring the
    trustedzone-attested DO owner the real securelock receives (available to any
    payload via task_caller(), and used by the ESR ACL). Resolution order:

      1. --caller / ECLD_TEST_CALLER (explicit override).
      (a) WALLET_ADDRESS stored in plain text in .config.json (written at
          publish time) -- used directly.
      (b) A plaintext private key in ECLD_PRIVATE_KEY -> derive the address.
      (c) An ENCRYPTED private key (ENC_PRIVATE_KEY in .config.json) -> derive,
          asking for the password (ECLD_KEY_PASSWORD, else an interactive prompt).
      Otherwise -> DEFAULT_TEST_CALLER (a well-known dev address).

    Also persists a resolved address to WALLET_ADDRESS so later runs skip the
    key handling entirely.
    """
    explicit = explicit or os.environ.get("ECLD_TEST_CALLER")
    if explicit:
        return explicit.strip()

    try:
        from ethernity_cloud_sdk_py.commands.config import Config
        cfg = Config(".config.json")
        cfg.load()
    except Exception:
        cfg = None

    # (a) plaintext address already stored
    if cfg is not None:
        addr = cfg.read("WALLET_ADDRESS")
        if addr:
            return str(addr).strip()

    try:
        from ethernity_cloud_sdk_py.commands.private_key import PrivateKeyManager
        priv = os.environ.get("ECLD_PRIVATE_KEY", "").strip()  # (b)
        if not priv and cfg is not None:                        # (c)
            enc = cfg.read("ENC_PRIVATE_KEY")
            if enc:
                pwd = os.environ.get("ECLD_KEY_PASSWORD")
                if pwd is None:
                    try:
                        import getpass
                        pwd = getpass.getpass(
                            "Private key password (to derive your developer "
                            "address; blank to use the default test address): ")
                    except Exception:
                        pwd = ""
                if pwd:
                    priv = PrivateKeyManager(pwd).decrypt_private_key(enc)
        if priv:
            addr = PrivateKeyManager("").extract_address_from_private_key(priv)
            # Persist so later runs are password-free (dev-address setup).
            if cfg is not None:
                try:
                    cfg.write("WALLET_ADDRESS", addr)
                except Exception:
                    pass
            return addr
    except Exception:
        pass

    # Nothing configured -> a stable default so local tests still have a caller.
    print(f"[ecld-test] no developer address configured; using default test "
          f"caller {DEFAULT_TEST_CALLER} (set ECLD_TEST_CALLER or WALLET_ADDRESS)")
    return DEFAULT_TEST_CALLER


def _serve(project_src, host, port, caller=None):
    """Start the local test API (vendored local_api.py) with the project backend."""
    for mod in ("serverless", "serverless.backend", "etny_exec", "local_api"):
        sys.modules.pop(mod, None)
    vendored_dir = os.path.dirname(os.path.normpath(VENDORED_EXEC))
    if project_src and os.path.isdir(project_src):
        sys.path.insert(0, project_src)
    sys.path.insert(0, vendored_dir)
    import local_api  # noqa: E402  (resolved from the vendored securelock src)

    backend_path = os.path.join(project_src, "serverless", "backend.py")
    if os.path.isfile(backend_path):
        print(f"backend : {backend_path}")
    else:
        print("backend : none found — serving with bare globals, like a stock enclave")
    try:
        local_api.serve(host=host, port=port, caller=caller, esr=True)
    except KeyboardInterrupt:
        print("\n[local-api] stopped")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ecld-test",
        description="Run a payload locally with the enclave's own executor (no SGX, no gas).",
    )
    parser.add_argument("code", nargs="*", help="payload code string, e.g. 'hello(\"World\")' — or the subcommand 'serve'")
    parser.add_argument("--file", "-f", help="read the payload from a file instead")
    parser.add_argument("--input", "-i", dest="input_file", help="file whose content becomes ___etny_data_set___")
    parser.add_argument("--input-text", dest="input_text", help="literal string for ___etny_data_set___")
    parser.add_argument("--expect", help="exact expected result; exit non-zero on mismatch (ECLD_TEST_EXPECT)")
    parser.add_argument("--src", default="src", help="project source dir containing serverless/backend.py (default: src)")
    parser.add_argument("--host", default="127.0.0.1", help="serve: bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8745, help="serve: port (default 8745)")
    parser.add_argument("--caller", help="task caller address (default: your developer address; ECLD_TEST_CALLER)")
    args = parser.parse_args(argv)

    caller = _resolve_caller(args.caller)

    if args.code and args.code[0] == "serve":
        return _serve(os.path.abspath(args.src), args.host, args.port, caller=caller)

    # RFC §9 env equivalents: flag -> env -> error/prompt-free default.
    env = os.environ.get
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            payload = fh.read()
    elif args.code:
        payload = " ".join(args.code)
    elif env("ECLD_TEST_CODE", "").strip():
        payload = env("ECLD_TEST_CODE").strip()
    else:
        parser.error("provide a payload string, --file, or ECLD_TEST_CODE")

    input_data = None
    if args.input_file is not None and args.input_text is not None:
        parser.error("--input and --input-text are mutually exclusive")
    if args.input_file is not None:
        with open(args.input_file, encoding="utf-8") as fh:
            input_data = fh.read()
    elif args.input_text is not None:
        input_data = args.input_text
    elif env("ECLD_TEST_INPUT") is not None:
        input_data = env("ECLD_TEST_INPUT")

    expected = args.expect if args.expect is not None else env("ECLD_TEST_EXPECT")

    project_src = os.path.abspath(args.src)
    backend_path = os.path.join(project_src, "serverless", "backend.py")
    if os.path.isfile(backend_path):
        print(f"backend : {backend_path}")
    else:
        # Same situation as targeting a stock enclave (e.g. etny-pynithy-testnet):
        # only ___etny_result___ / ___etny_data_set___ are in scope.
        print("backend : none found — running with bare globals, like a stock enclave")

    executor = _load_enclave_executor(project_src)

    # ESR emulation ON by default so a state-using backend runs locally exactly
    # as it would in an ESR-enabled enclave (no chain, node, or SGX). The caller
    # (default: your developer address) is set for every task, so task_caller()
    # and the state ACL behave as they do on-chain.
    try:
        import ecld_state
        import esr_local
        esr_local.install(ecld_state, caller=caller, file_prefix=".ecld-esr-local")
        ecld_state._task_caller = ecld_state._norm_addr(caller) if caller else None
        print(f"esr     : local emulation ON   caller {caller or '(none)'}")
    except Exception as e:
        print(f"esr     : emulation unavailable ({e})")

    print(f"payload : {payload!r}")
    print(f"input   : {'<empty>' if input_data is None else repr(input_data[:80])}")
    print("--- enclave executor ---")
    code, result = executor.execute_task_v3(payload, input_data)
    print("--- end ---")

    name = TASK_STATUS_NAMES.get(code, f"UNKNOWN_{code}")
    print(f"task code: {code} ({name})")
    print(f"result   : {result!r}")
    if code != 0:
        print(
            "\nThis is exactly what the network would write on-chain — the failed"
            "\nattempt would still cost gas. Fix locally, then submit."
        )
        return 1
    if expected is not None:
        actual = result if isinstance(result, str) else repr(result)
        if actual != expected:
            print(f"\nEXPECT MISMATCH:\n  expected: {expected!r}\n  actual  : {actual!r}")
            return 1
        print("expect   : matched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
