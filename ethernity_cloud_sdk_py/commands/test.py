"""ecld-test — run a payload locally, without SGX, exactly as the enclave would.

Executes the code string against the project's src/serverless/backend.py using
the SAME vendored executor (etny_exec.py) that ecld-build bakes into the
securelock enclave: same function reflection, same ___etny_result___ /
___etny_data_set___ globals, same expression auto-wrap, same TaskStatus codes.

This validates the CALL — function names, arguments, result wiring — before a
single on-chain request is paid for. It does not validate SGX attestation,
checksums, or encryption; those need a real (testnet) run.

Usage, from the project root (where .config.json lives):

    ecld-test 'hello("World")'
    ecld-test --file payload.py
    ecld-test --input data.json 'process(___etny_data_set___)'
    ecld-test --input-text '{"x": 1}' 'process(___etny_data_set___)'

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

    path = os.path.normpath(VENDORED_EXEC)
    if not os.path.isfile(path):
        raise RuntimeError(f"vendored enclave executor not found at {path}")
    spec = importlib.util.spec_from_file_location("_ecld_local_etny_exec", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ecld-test",
        description="Run a payload locally with the enclave's own executor (no SGX, no gas).",
    )
    parser.add_argument("code", nargs="*", help="payload code string, e.g. 'hello(\"World\")'")
    parser.add_argument("--file", "-f", help="read the payload from a file instead")
    parser.add_argument("--input", "-i", dest="input_file", help="file whose content becomes ___etny_data_set___")
    parser.add_argument("--input-text", dest="input_text", help="literal string for ___etny_data_set___")
    parser.add_argument("--src", default="src", help="project source dir containing serverless/backend.py (default: src)")
    args = parser.parse_args(argv)

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            payload = fh.read()
    elif args.code:
        payload = " ".join(args.code)
    else:
        parser.error("provide a payload string or --file")

    input_data = None
    if args.input_file is not None and args.input_text is not None:
        parser.error("--input and --input-text are mutually exclusive")
    if args.input_file is not None:
        with open(args.input_file, encoding="utf-8") as fh:
            input_data = fh.read()
    elif args.input_text is not None:
        input_data = args.input_text

    project_src = os.path.abspath(args.src)
    backend_path = os.path.join(project_src, "serverless", "backend.py")
    if os.path.isfile(backend_path):
        print(f"backend : {backend_path}")
    else:
        # Same situation as targeting a stock enclave (e.g. etny-pynithy-testnet):
        # only ___etny_result___ / ___etny_data_set___ are in scope.
        print("backend : none found — running with bare globals, like a stock enclave")

    executor = _load_enclave_executor(project_src)

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
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
