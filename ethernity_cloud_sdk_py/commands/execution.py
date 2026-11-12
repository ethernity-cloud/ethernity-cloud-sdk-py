"""The execution object of a run: the EXECUTION block of .config.json, the
EXEC_* variables ecld-publish exports to src/.env, and the nested
`execution` of the resources ecld-run and the template pass to the runner.

The keys, types, defaults, bounds and error messages are those of
tests/spec/execution-spec.json (the copy of pox-smart-contract-litvm's
spec/execution-spec.json), which both runners and both SDKs share:

- a missing object or key takes its default; None is not a value;
- strings (an EXEC_* value, a hand-edited .config.json) are parsed into
  numbers and booleans before validation;
- an unknown key at any level is an error;
- the session block is accepted in both modes and applied in mode "session"
  only, so a block written by ecld-init carries every key;
- the object's shape is checked first (unknown keys at the top level, then
  the session and esr blocks being objects without unknown keys), then the
  values in schema order; the first failing check names the error. The JS
  SDK's execution.js checks in the same order.
"""

import copy
import re

DEFAULTS = {
    "mode": "task",
    "durationHours": 1,
    "parallelism": 1,
    "maxCu": 0,
    "headroomPercent": 25,
    "session": {
        "expectedInputs": 1,
        "keepOpen": False,
        "renew": False,
        "maxRenewals": 0,
    },
    "esr": {
        "expectedCommits": 0,
    },
}

# One row per key, in the fixture's order; `message` is the fixture's error
# text for a value outside the row's type or bounds.
SCHEMA = (
    {"key": "mode", "type": "string", "values": ("task", "session"), "default": "task",
     "message": 'execution.mode must be "task" or "session"'},
    {"key": "durationHours", "type": "integer", "min": 1, "max": 65535, "default": 1,
     "message": "execution.durationHours must be an integer between 1 and 65535"},
    {"key": "parallelism", "type": "integer", "min": 1, "max": None, "default": 1,
     "message": "execution.parallelism must be an integer >= 1"},
    {"key": "maxCu", "type": "integer", "min": 0, "max": None, "default": 0,
     "message": "execution.maxCu must be an integer >= 0 (thousandths of a CU, 0 = any node)"},
    {"key": "headroomPercent", "type": "integer", "min": 0, "max": 100, "default": 25,
     "message": "execution.headroomPercent must be an integer between 0 and 100"},
    {"key": "session.expectedInputs", "type": "integer", "min": 1, "max": 256, "default": 1,
     "message": "execution.session.expectedInputs must be an integer between 1 and 256"},
    {"key": "session.keepOpen", "type": "boolean", "default": False,
     "message": "execution.session.keepOpen must be true or false"},
    {"key": "session.renew", "type": "boolean", "default": False,
     "message": "execution.session.renew must be true or false"},
    {"key": "session.maxRenewals", "type": "integer", "min": 0, "max": None, "default": 0,
     "message": "execution.session.maxRenewals must be an integer >= 0"},
    {"key": "esr.expectedCommits", "type": "integer", "min": 0, "max": 100, "default": 0,
     "message": "execution.esr.expectedCommits must be an integer between 0 and 100"},
)

RENEW_MESSAGE = "execution.session.maxRenewals must be >= 1 when execution.session.renew is true"

# The EXEC_* variable of each key, in schema order: the list ecld-publish
# writes to src/.env and the template reads back (the same list in the JS SDK).
ENV_KEYS = (
    ("EXEC_MODE", "mode"),
    ("EXEC_DURATION_HOURS", "durationHours"),
    ("EXEC_PARALLELISM", "parallelism"),
    ("EXEC_MAX_CU", "maxCu"),
    ("EXEC_HEADROOM_PERCENT", "headroomPercent"),
    ("EXEC_SESSION_EXPECTED_INPUTS", "session.expectedInputs"),
    ("EXEC_SESSION_KEEP_OPEN", "session.keepOpen"),
    ("EXEC_SESSION_RENEW", "session.renew"),
    ("EXEC_SESSION_MAX_RENEWALS", "session.maxRenewals"),
    ("EXEC_ESR_EXPECTED_COMMITS", "esr.expectedCommits"),
)

# Relays an order with ESR enabled adds per hour to its 1 + expectedInputs
# rows (the etny-sw row plus one etny-so per input).
ESR_RELAYS_PER_HOUR = 10

_TRUE = ("true", "1", "yes", "y", "on")
_FALSE = ("false", "0", "no", "n", "off")
# The decimal strings both SDKs read as numbers: digits with an optional
# fraction, no exponent (the JS SDK's fromEnv takes the same grammar).
_INTEGER = re.compile(r"[+-]?\d+")
_NUMBER = re.compile(r"[+-]?\d+(\.\d+)?")

_ROWS = {row["key"]: row for row in SCHEMA}


class ExecutionError(ValueError):
    """An execution object outside the schema; str() is the fixture's message."""


def _get(obj, dotted):
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None, False
        obj = obj[part]
    return obj, True


def _set(obj, dotted, value):
    parts = dotted.split(".")
    for part in parts[:-1]:
        obj = obj.setdefault(part, {})
    obj[parts[-1]] = value


def parse_value(dotted, text):
    """The value of the schema key `dotted` parsed from the string `text`: a
    boolean for a boolean key, an int or float (a decimal string, no
    exponent) for an integer key, and the string itself when it reads as
    neither, so validate() names the error."""
    if not isinstance(text, str):
        return text
    row = _ROWS.get(dotted)
    stripped = text.strip()
    if row is None or row["type"] == "string":
        return stripped
    if row["type"] == "boolean":
        lowered = stripped.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        return stripped
    if _INTEGER.fullmatch(stripped):
        return int(stripped)
    if _NUMBER.fullmatch(stripped):
        return float(stripped)
    return stripped


def _parse_block(block, prefix):
    if not isinstance(block, dict):
        return block
    parsed = {}
    for key, value in block.items():
        dotted = prefix + key
        if isinstance(value, dict):
            parsed[key] = _parse_block(value, dotted + ".")
        else:
            parsed[key] = parse_value(dotted, value)
    return parsed


def parse_block(block):
    """The EXECUTION block of .config.json with its string values parsed per
    key; unknown keys and non-string values are kept as they are, and a
    missing block is an empty object."""
    if block is None:
        return {}
    return _parse_block(block, "")


def from_env(env):
    """The execution object the EXEC_* variables of `env` (a mapping such as
    os.environ or a parsed .env) describe: every set, non-empty variable of
    ENV_KEYS parsed per key, every other key left to its default."""
    execution = {}
    for env_key, dotted in ENV_KEYS:
        text = env.get(env_key)
        if text is None or (isinstance(text, str) and text.strip() == ""):
            continue
        _set(execution, dotted, parse_value(dotted, text))
    return execution


def to_env(execution):
    """The EXEC_* variables of an execution object, validated first, in
    ENV_KEYS order, every value a string (booleans as "true" / "false").
    Raises ExecutionError as validate() does."""
    complete_object = validate(execution)
    out = {}
    for env_key, dotted in ENV_KEYS:
        value, _ = _get(complete_object, dotted)
        if isinstance(value, bool):
            out[env_key] = "true" if value else "false"
        else:
            out[env_key] = str(value)
    return out


def complete(partial):
    """DEFAULTS with `partial`'s values over them: a complete object whose
    unknown keys are kept, so validate() still names them."""
    merged = copy.deepcopy(DEFAULTS)
    if not isinstance(partial, dict):
        return partial

    def merge(into, over):
        for key, value in over.items():
            if isinstance(value, dict) and isinstance(into.get(key), dict):
                merge(into[key], value)
            else:
                into[key] = value

    merge(merged, partial)
    return merged


def _integer(value):
    """The int of an integer value, None for anything else (a bool is not an
    integer; a float with no fraction is its int, as in JSON)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _unknown_keys(block, prefix, known):
    for key in block:
        if key not in known:
            raise ExecutionError('execution%s has an unknown key "%s"' % (prefix, key))


def validate(execution=None):
    """The complete, checked execution object for `execution` (None or a
    partial object): defaults filled, every value in its type and bounds.
    Raises ExecutionError with the fixture's message of the first failing
    check: the shape (unknown top-level keys, then each of the session and
    esr blocks being an object without unknown keys), then the values in
    schema order."""
    if execution is None:
        execution = {}
    if not isinstance(execution, dict):
        raise ExecutionError("execution must be an object")
    top_level = {row["key"].split(".")[0] for row in SCHEMA}
    _unknown_keys(execution, "", top_level)
    for name in ("session", "esr"):
        if name in execution:
            block = execution[name]
            if not isinstance(block, dict):
                raise ExecutionError("execution.%s must be an object" % name)
            known = {r["key"].split(".")[1] for r in SCHEMA if r["key"].startswith(name + ".")}
            _unknown_keys(block, "." + name, known)
    out = copy.deepcopy(DEFAULTS)
    for row in SCHEMA:
        dotted = row["key"]
        value, found = _get(execution, dotted)
        if found:
            if row["type"] == "string":
                if not isinstance(value, str) or value not in row["values"]:
                    raise ExecutionError(row["message"])
                checked = value
            elif row["type"] == "boolean":
                if not isinstance(value, bool):
                    raise ExecutionError(row["message"])
                checked = value
            else:
                checked = _integer(value)
                if checked is None or checked < row["min"] or (row["max"] is not None and checked > row["max"]):
                    raise ExecutionError(row["message"])
            _set(out, dotted, checked)
        if dotted == "session.maxRenewals" and out["session"]["renew"] and out["session"]["maxRenewals"] < 1:
            raise ExecutionError(RENEW_MESSAGE)
    return out


def normalize(resources):
    """A resources object split into its flat part and its complete, checked
    execution: a legacy flat `duration` becomes execution.durationHours, and
    execution.durationHours wins when both are given. Anything but an object
    (None, as runner.run takes an absent resources) is an empty one; an
    `execution` key set to a non-object, None included, is an error. Returns
    {"resources": flat, "execution": execution}."""
    if not isinstance(resources, dict):
        resources = {}
    flat = {key: value for key, value in resources.items() if key not in ("duration", "execution")}
    execution = resources["execution"] if "execution" in resources else {}
    if not isinstance(execution, dict):
        raise ExecutionError("execution must be an object")
    execution = copy.deepcopy(execution)
    if "duration" in resources and "durationHours" not in execution:
        execution["durationHours"] = resources["duration"]
    return {"resources": flat, "execution": validate(execution)}


def relays_per_hour(execution, esr_enabled):
    """The relays an order of this execution needs per hour of its duration:
    1 + session.expectedInputs rows, plus ESR_RELAYS_PER_HOUR commits when
    the dApp has ESR enabled. A value the user gives wins over this default."""
    rows = 1 + validate(execution)["session"]["expectedInputs"]
    return rows + (ESR_RELAYS_PER_HOUR if esr_enabled else 0)
