import os.path
import ast
import json
import logging
import traceback

# If the serverless backend fails to import, remember WHY. Previously any
# import failure was silently swallowed (backend = None), so every task then
# died later with a misleading "name 'X' is not defined" — a missing module in
# requirements.txt or a top-level-style import inside backend.py (the enclave
# loads it as the package `serverless.backend`) took many builds to diagnose.
# Only a genuinely absent backend (no serverless module at all) stays silent:
# that is the stock-enclave configuration, where payloads are self-contained.
_backend_import_error = None
try:
    import serverless.backend as backend
except ModuleNotFoundError as e:
    backend = None
    if e.name not in ("serverless", "serverless.backend"):
        _backend_import_error = f"{type(e).__name__}: {e}"
except BaseException as e:  # SyntaxError, ValueError at import time, etc.
    backend = None
    _backend_import_error = f"{type(e).__name__}: {e}"

sdkFunctions = {}
if backend is not None:
    for func in backend.__dict__.keys():
        if func not in backend.__builtins__.keys() and func not in [
            "__file__",
            "__cached__",
            "__builtins__",
        ]:
            sdkFunctions.update({func: backend.__dict__[func]})


def _encode_result_data(data):
    """Automatic encoding for task results (single code path for all results).

    Returns (type, encoded): JSON-serializable values ride as JSON, strings as
    text, bytes-like as base64. Anything else is JSON-dumped with a stringify
    fallback -- a task result never fails on encoding.
    """
    import base64 as _b64
    if isinstance(data, (bytes, bytearray, memoryview)):
        return "base64", _b64.b64encode(bytes(data)).decode("ascii")
    if isinstance(data, str):
        return "text", data
    try:
        json.dumps(data)
        return "json", data
    except (TypeError, ValueError):
        try:
            return "json", json.loads(json.dumps(data, default=str))
        except Exception:
            return "text", str(data)


def ecld_result(data=None, *, state=True, keys=None):
    """Return a task result (THE way to end a task).

    Builds the structured result envelope and terminates execution with it:

        {"ecld": 1, "type": "json"|"text"|"base64", "data": ..., "esr": ...}

    - `data` is encoded automatically (see _encode_result_data).
    - `state=True` (default) attaches the ESR state of every key this task
      touched; `state="meta"` attaches {key, version, cid} without the state
      payload; `state=False` attaches nothing.
    - `keys=[...]` restricts the attachment to those keys, force-reading any
      the task did not touch.

    ESR attachment is best-effort: an ESR-disabled build (or any attachment
    error) yields `esr: null`, never a failed task.
    """
    rtype, rdata = _encode_result_data(data)
    envelope = {"ecld": 1, "type": rtype, "data": rdata, "esr": None}
    if state or keys:
        try:
            import ecld_state
            envelope["esr"] = ecld_state.ledger_snapshot(
                include_state=(state is True), keys=keys)
        except Exception:
            envelope["esr"] = None
    quit([0, json.dumps(envelope, default=str)])


def ___etny_result___(data):
    """Legacy alias: same builder, ESR attachment off -- today's behavior."""
    return ecld_result(data, state=False)


def esr_fetch(*keys):
    """Standard state-fetch task body: no computation, just attach state.

    The runner's cache-gated read submits this on a cache miss, so dApps never
    write their own read function: `esr_fetch("profile-7")`.
    """
    return ecld_result(None, keys=list(keys) or None)


class TaskStatus:
    SUCCESS = 0
    SYSTEM_ERROR = 1
    KEY_ERROR = 2
    SYNTAX_WARNING = 3
    BASE_EXCEPTION = 4
    PAYLOAD_NOT_DEFINED = 5
    PAYLOAD_CHECKSUM_ERROR = 6
    INPUT_CHECKSUM_ERROR = 7
    EXECVE = 8
    # Extended diagnostics (match the trustedzone's extended enum): the
    # serverless backend failed to import inside the enclave, so none of its
    # functions exist. Reported eagerly with the original import error instead
    # of letting every call die as "name 'X' is not defined".
    IMPORT_ERROR = 28
    # A required enclave config value is present but EMPTY (e.g. an ESR
    # address that was never baked into the sealed image). Reported eagerly
    # with the variable named, instead of a confusing downstream crash.
    CONFIG_ERROR = 32
    EXECUTION_TIMEOUT = 33       # Started but produced no result within the order duration.
    ESR_GAS_LIMIT_EXCEEDED = 34  # ESR state commits would exceed the per-order relayed-gas budget.
    ESR_NONCE_VIOLATION = 36     # A commit's idempotency nonce was already used -- duplicate
                                 # suppressed, state unchanged (StateNonceError).
    ESR_RELAY_TIMEOUT = 37       # The enclave signed state commits but they did not land on
                                 # the registry within 5 blocks -- the node did not relay them.
                                 # The result is NOT trustworthy as persisted state; the
                                 # validator refunds the order.
    ESR_COMMIT_LIMIT_EXCEEDED = 38  # More than 100 state commits in one run -- the per-run cap
                                 # that bounds relayed transactions and the result size.
    SECURITY_VIOLATION = 35      # A state commit was authorized under a caller other than the
                                 # task's submitter (the in-enclave ownership check was bypassed).
                                 # Set by the securelock when it detects a forged commit caller,
                                 # and independently by the trustedzone re-adjudication.
    CAS_ATTESTATION_FAULT = 40   # The CAS that provisioned this enclave presented an
                                 # INVALID self-attestation quote (ECAS_CAS_QUOTE failing
                                 # the ValidatorRegistry checks). A missing quote is a
                                 # rollout gap and only logs; an INCORRECT one means the
                                 # operator provisioned the task through an impostor CAS.
                                 # Operator fault; the order terminates and is refunded.


def _empty_required_config():
    """Names of *_ADDRESS-style enclave config vars that are set but empty.

    The enclave is sealed: env can only come from the image, so a present-but-
    empty required value is always a build/render defect, never a runtime
    choice. ESR_* vars only exist when the project enabled ESR at build time.
    """
    required = ("ESR_CONTRACT_ADDRESS",)
    return [name for name in required
            if name in os.environ and not os.environ[name].strip()]


def execute_task_v3(payload_data, input_data, extra_globals=None):
    missing = _empty_required_config()
    if missing:
        return (
            TaskStatus.CONFIG_ERROR,
            "ENCLAVE CONFIG ERROR: required value(s) empty inside the enclave: "
            + ", ".join(missing)
            + " | The enclave is sealed, so this value had to be baked at build"
            + " time and was not. Re-run ecld-build (it validates ESR config)"
            + " and republish.",
        )
    if _backend_import_error is not None:
        return (
            TaskStatus.IMPORT_ERROR,
            "BACKEND IMPORT ERROR: " + _backend_import_error
            + " | The serverless backend failed to import inside the enclave, so"
            + " none of its functions are available. Common causes: a module"
            + " missing from src/serverless/requirements.txt, or a top-level"
            + " import inside backend.py (the enclave loads it as the package"
            + " 'serverless.backend' - use relative imports for sibling modules).",
        )
    base_globals = session_base_globals()
    if extra_globals:
        base_globals.update(extra_globals)
    return Exec(payload_data, input_data, globals=base_globals)


class _Ecld:
    """The single dApp-facing handle injected into every payload as `ecld`.

    A namespaced front door over the same primitives a payload used to reach
    by bare magic names, chosen to read like a contract object to Web3 devs:

        ecld.result(value)        # end the task with a result (+ESR state)
        ecld.input                # the request payload (was ___etny_data_set___)
        ecld.caller               # the data owner's wallet (msg.sender-style)
        ecld.on_input = fn        # session input handler (was ___etny_on_input___)
        ecld.state.get(k) / .commit(k, v) / .grant(...) / ...   # ESR
        ecld.fetch("k1", "k2")    # standard state-fetch task body

    Every legacy name (___etny_result___, ___etny_data_set___,
    ___etny_on_input___, task_caller, esr_*, ...) still works unchanged; this
    object adds a coherent surface without removing anything. `input` and the
    session handler are populated per-run by the executor.
    """

    def __init__(self):
        self.result = ecld_result
        self.fetch = esr_fetch
        self.input = None          # set by Exec() when input_data is present
        self.on_input = None       # session handler slot; payload assigns it
        self.state = None          # StateRegistry-style handle if ESR present
        self._caller_fn = None     # bound to ecld_state.task_caller if present

    @property
    def caller(self):
        """The verified wallet that submitted this task (msg.sender-style),
        or None in a non-ESR build / local test. Live: reads the attested
        caller at access time, not at scope-build time."""
        fn = self._caller_fn
        try:
            return fn() if callable(fn) else None
        except Exception:
            return None


def session_base_globals():
    """The exec globals every payload run receives. Public so the session
    executor (etny_session_exec) builds ONE persistent dict and keeps payload
    state alive across streamed inputs."""
    ecld = _Ecld()
    base_globals = {
        "___etny_result___": ___etny_result___,   # legacy alias (no ESR)
        "ecld_result": ecld_result,               # the result API (bare)
        "esr_fetch": esr_fetch,                   # standard state-fetch task
        "ecld": ecld,                             # the namespaced handle
        **sdkFunctions,
    }
    # State ownership / ACL API (present only in ESR-enabled builds). Enforced
    # inside ecld_state against the trustedzone-attested caller.
    try:
        import ecld_state as _ecld_state
        base_globals.setdefault("task_caller", _ecld_state.task_caller)
        for _name in ("esr_grant", "esr_revoke", "esr_set_public_read",
                      "esr_transfer", "esr_owner", "esr_acl", "esr_nonce"):
            base_globals.setdefault(_name, getattr(_ecld_state, _name))
        # Mirror the ESR surface onto the namespaced handle. `caller` is a live
        # property that calls task_caller() at access time (see _Ecld.caller).
        ecld._caller_fn = _ecld_state.task_caller
        try:
            ecld.state = _ecld_state.StateRegistry()
        except Exception:
            ecld.state = None
    except Exception:
        pass
    return base_globals

def Exec(payload_data, input_data, globals=None, locals=None):
    try:
        if globals is None:
            globals = {}
        if locals is None:
            locals = globals

        print("Globals keys:", list(globals.keys()))
        print("Locals keys:", list(locals.keys()))
        print("execve in globals:", "execve" in globals)
        print("execve in locals:", "execve" in locals)

        if payload_data is not None:
            if input_data is not None:
                globals["___etny_data_set___"] = input_data   # legacy name
                _ecld = globals.get("ecld")
                if _ecld is not None:
                    _ecld.input = input_data                  # ecld.input
            module = ast.parse(payload_data)
            outputs = []
            for node in module.body:
                if isinstance(node, ast.Expr):
                    expr_code = compile(
                        ast.Expression(node.value), filename="<ast>", mode="eval"
                    )
                    result = eval(expr_code, globals, locals)
                    outputs.append(result)
                else:
                    # Handle statements if needed
                    exec(
                        compile(ast.Module([node], type_ignores=[]), filename="<ast>", mode="exec"),
                        globals,
                        locals,
                    )

            # Function results are arbitrary Python values -- the shipped
            # esr-counter example returns dicts -- so serialize anything that
            # is not already a str instead of letting join() raise. None (an
            # expression statement with no value, e.g. a bare print()) is
            # dropped, matching REPL semantics. NOTE: this is the executor the
            # serverless securelock actually runs (local_api.py imports
            # etny_exec); the 0.8.3 fix landed only in etny_exec_serv.py,
            # which nothing imports.
            rendered = [
                out if isinstance(out, str) else json.dumps(out, default=str)
                for out in outputs
                if out is not None
            ]
            return ___etny_result___("\n".join(rendered))
        else:
            return (
                TaskStatus.PAYLOAD_NOT_DEFINED,
                "Could not find the source file to execute",
            )

        return TaskStatus.SUCCESS, "TASK EXECUTED SUCCESSFULLY"
    except SystemExit as e:
        # A task returns its result ONLY through ___etny_result___(data) ->
        # quit([code, data]), which raises SystemExit carrying [code, data].
        # Decode and honor that embedded code -- it is the authoritative outcome.
        #
        # Handled BEFORE `except SystemError` below on purpose. The read path runs
        # web3/crypto C-extensions before returning; those intermittently raise a
        # spurious SystemError ("error return without exception set") during the
        # SystemExit unwind. With SystemError caught first, that spurious error
        # stamped SYSTEM_ERROR onto a task that had already computed the correct
        # value -- the ~50% false failure seen only on read tasks. Honoring the
        # real result first fixes it.
        try:
            code, data = e.args[0][0], e.args[0][1]
        except Exception:
            tb = traceback.format_exc()
            logging.error(
                "SystemExit without a [code, data] result payload: %r (args=%r)"
                " -- full traceback follows:\n%s", e, getattr(e, "args", None), tb)
            return TaskStatus.SYSTEM_ERROR, f"SYSTEM_ERROR: {e!r}\n{tb}"
        if code == 0:
            return TaskStatus.SUCCESS, data
        return int(code), data
    except SystemError as e:
        # A spurious native/interpreter SystemError can surface DURING the
        # SystemExit unwind and REPLACE it, so the exception reaching here is a
        # SystemError even though the task already produced its result via
        # quit([code, data]). The real SystemExit is still on the chain as
        # e.__context__ / e.__cause__ -- recover the result from there before
        # treating this as a failure.
        for chained in (getattr(e, "__context__", None), getattr(e, "__cause__", None)):
            if isinstance(chained, SystemExit):
                try:
                    code, data = chained.args[0][0], chained.args[0][1]
                except Exception:
                    continue
                logging.warning(
                    "SystemError raised during result unwind; recovered task "
                    "result from chained SystemExit (code=%r). Native error was: "
                    "%r", code, e)
                if code == 0:
                    return TaskStatus.SUCCESS, data
                return int(code), data
        # No recoverable result -> genuine internal error. Deliver the FULL
        # traceback to the data owner AS the result (visible in their runner
        # output) and never crash the enclave.
        tb = traceback.format_exc()
        logging.error(
            "SystemError in payload execution: %r (args=%r) -- full traceback "
            "follows:\n%s", e, getattr(e, "args", None), tb)
        return TaskStatus.SYSTEM_ERROR, f"SYSTEM_ERROR: {e!r}\n{tb}"
    except KeyError as e:
        return TaskStatus.KEY_ERROR, e.args[0]
    except SyntaxWarning as e:
        return TaskStatus.SYNTAX_WARNING, e.args[0]
    except BaseException as e:
        # A duplicate-suppressed commit (StateNonceError) gets its own task
        # code so the dApp can distinguish "already applied" from a failure.
        # Matched by name so non-ESR builds need no import.
        if type(e).__name__ == "StateNonceError":
            return TaskStatus.ESR_NONCE_VIOLATION, f"ESR_NONCE_VIOLATION: {e}"
        # Over the per-run commit cap (100): the over-limit commit was NOT
        # applied; the earlier commits stand and will be relayed.
        if type(e).__name__ == "StateLimitError":
            return (TaskStatus.ESR_COMMIT_LIMIT_EXCEEDED,
                    f"ESR_COMMIT_LIMIT_EXCEEDED: {e}")
        # Deliver the traceback to the data owner as the result and never crash;
        # preserve the embedded-result SUCCESS path (code 0).
        try:
            if e.args[0][0] == 0:
                return TaskStatus.SUCCESS, e.args[0][1]
        except Exception:
            pass
        tb = traceback.format_exc()
        logging.error(
            "BaseException in payload execution: %r -- full traceback follows:\n%s",
            e, tb)
        return TaskStatus.BASE_EXCEPTION, f"BASE_EXCEPTION: {e!r}\n{tb}"

