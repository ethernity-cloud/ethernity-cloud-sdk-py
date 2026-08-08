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
}


def _challenge(length=20):
    # Same shape as the trustedzone's enclave challenge (base36).
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def run_task(payload, input_data=None):
    """One task through the real executor; returns the API response dict."""
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
            self._send(200, run_task(payload, input_data))
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON body"})
        except Exception as e:  # harness fault, not a task result
            self._send(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        print(f"[local-api] {self.address_string()} {fmt % args}")


def serve(host="127.0.0.1", port=8745):
    funcs = sorted(getattr(etny_exec, "sdkFunctions", {}).keys())
    print(f"[local-api] backend functions: {funcs if funcs else 'none (stock-enclave mode)'}")
    print(f"[local-api] listening on http://{host}:{port}  (POST /v1/task, GET /v1/health)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
