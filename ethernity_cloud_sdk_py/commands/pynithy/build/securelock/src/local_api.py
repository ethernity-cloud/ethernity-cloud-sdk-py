"""Local test API over the securelock executor. NOT used inside the enclave.

This file lives in the securelock source tree on purpose: it imports and reuses
the exact same etny_exec module that runs inside the SGX enclave, so a payload
tested against this API is executed by the same code, with the same globals and
the same TaskStatus semantics, as a real on-chain task. What it deliberately
skips is everything around execution: attestation, checksums, encryption,
IPFS, and the blockchain.

Launched by `ecld-test serve` (which puts the project's src/ on sys.path so
`serverless.backend` resolves to the developer's backend, exactly as inside
binary-fs). The runners' LOCAL mode talks to it:

    GET  /v1/health -> {"status": "ok", "backend": [...function names...]}
    POST /v1/task   {"payload": "...", "input": "..."|null}
                    -> {"task_code": 0, "task_code_name": "SUCCESS",
                        "result": "...", "checksum": "<sha256(result)>",
                        "enclave_challenge": "<20 chars>",
                        "result_string": "v3:<code>:<checksum>:<challenge>:"}

The enclave is stateless per task and so is this server: every POST is one
isolated execution.
"""

import hashlib
import json
import random
import string
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import etny_exec

# Local ESR emulation. ON BY DEFAULT so `ecld-test` matches a real ESR-enabled
# build with no extra setup: a StateRegistry() in the backend works fully
# in-process against an in-memory registry + store -- no chain, node, or SGX.
# See esr_local.py. Disabled only if installation fails (e.g. no web3 dep).
_ESR = {"installed": False, "default_caller": None, "handles": None}

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
}


def _challenge(length=20):
    # Same shape as the trustedzone's enclave challenge (base36).
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def _install_esr(default_caller=None, persist=True):
    """Install the local ESR emulator into ecld_state (best-effort). ON by
    default so ecld-test matches a real ESR-enabled build."""
    try:
        import ecld_state
        import esr_local
        handles = esr_local.install(
            ecld_state, caller=default_caller,
            file_prefix=".ecld-esr-local" if persist else None)
        _ESR["installed"] = True
        _ESR["default_caller"] = default_caller
        _ESR["handles"] = handles
        return True
    except Exception as e:
        _ESR["installed"] = False
        print(f"[local-api] ESR emulation unavailable: {e}")
        return False


def _set_caller(caller):
    """Set the task caller EXACTLY as the real securelock does after the
    trustedzone hands it the DO owner -- available to any payload via
    task_caller(), and used by the ESR ACL. Applies whether or not the payload
    uses ESR."""
    try:
        import ecld_state
        ecld_state._task_caller = ecld_state._norm_addr(caller) if caller else None
    except Exception:
        pass


def run_task(payload, input_data=None, caller=None):
    """One task through the real executor; returns the API response dict.

    `caller` mirrors the trustedzone-attested DO owner the real securelock
    receives: it is set for EVERY task (not just ESR ones), so task_caller()
    behaves locally as it does in the enclave. Falls back to the server's
    default caller when a request does not specify one."""
    effective_caller = caller if caller is not None else _ESR.get("default_caller")
    _set_caller(effective_caller)
    code, result = etny_exec.execute_task_v3(payload, input_data)
    result_text = result if isinstance(result, str) else repr(result)
    checksum = hashlib.sha256(result_text.encode("utf-8")).hexdigest()
    challenge = _challenge()
    return {
        "task_code": code,
        "task_code_name": TASK_STATUS_NAMES.get(code, f"UNKNOWN_{code}"),
        "result": result_text,
        "checksum": checksum,
        "enclave_challenge": challenge,
        "result_string": f"v3:{code}:{checksum}:{challenge}:",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ecld-local-api/1.0"

    def _send(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Browser dApps (JS runner local mode) call this from another origin.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path == "/v1/health":
            funcs = sorted(getattr(etny_exec, "sdkFunctions", {}).keys())
            self._send(200, {"status": "ok", "backend": funcs})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/task":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            payload = req.get("payload")
            if not isinstance(payload, str) or not payload.strip():
                self._send(400, {"error": "payload (string) is required"})
                return
            input_data = req.get("input")
            if input_data is not None and not isinstance(input_data, str):
                input_data = json.dumps(input_data)
            # A request may name the task caller (the DO owner the trustedzone
            # would attest); else the server's default caller is used.
            caller = req.get("caller")
            self._send(200, run_task(payload, input_data, caller=caller))
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON body"})
        except Exception as e:  # harness fault, not a task result
            self._send(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        print(f"[local-api] {self.address_string()} {fmt % args}")


def serve(host="127.0.0.1", port=8745, caller=None, esr=True, persist=True):
    funcs = sorted(getattr(etny_exec, "sdkFunctions", {}).keys())
    print(f"[local-api] backend functions: {funcs if funcs else 'none (stock-enclave mode)'}")
    if esr:
        if _install_esr(default_caller=caller, persist=persist):
            wallet = None
            try:
                import ecld_state
                wallet = ecld_state.StateRegistry().wallet_address
            except Exception:
                pass
            print(f"[local-api] ESR emulation: ON (in-process, no chain)"
                  + (f"  enclave wallet {wallet}" if wallet else ""))
            caller_note = caller or "(none -- pass 'caller' per request)"
            print(f"[local-api] task caller: {caller_note}")
            if persist:
                print("[local-api] ESR state persists in .ecld-esr-local.*.json")
    print(f"[local-api] listening on http://{host}:{port}  (POST /v1/task, GET /v1/health)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
