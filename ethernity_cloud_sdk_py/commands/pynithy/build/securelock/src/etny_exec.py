import os.path
import ast
import json

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


def ___etny_result___(data):
    quit([0, data])


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
    base_globals = {"___etny_result___": ___etny_result___, **sdkFunctions}
    if extra_globals:
        base_globals.update(extra_globals)
    return Exec(payload_data, input_data, globals=base_globals)

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
                globals["___etny_data_set___"] = input_data
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
    except SystemError as e:
        return TaskStatus.SYSTEM_ERROR, e.args[0]
    except KeyError as e:
        return TaskStatus.KEY_ERROR, e.args[0]
    except SyntaxWarning as e:
        return TaskStatus.SYNTAX_WARNING, e.args[0]
    except BaseException as e:
        try:
            if e.args[0][0] == 0:
                return TaskStatus.SUCCESS, e.args[0][1]
            else:
                return TaskStatus.BASE_EXCEPTION, e.args[0]
        except Exception as e:
            return TaskStatus.BASE_EXCEPTION, e.args[0]

