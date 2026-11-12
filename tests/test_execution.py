"""commands/execution.py against tests/spec/execution-spec.json (the copy of
pox-smart-contract-litvm's fixture): the defaults and the schema rows, the
three normalization cases, the ten validation cases with their exact
messages, the EXEC_* parsing and export, and the relays-per-hour default."""
import json
from pathlib import Path

import pytest

from ethernity_cloud_sdk_py.commands import execution
from ethernity_cloud_sdk_py.commands.execution import ExecutionError

SPEC = json.loads((Path(__file__).resolve().parent / "spec" / "execution-spec.json").read_text(encoding="utf-8"))


def test_defaults_are_the_fixtures():
    assert execution.DEFAULTS == SPEC["defaults"]
    assert execution.validate() == SPEC["defaults"]
    assert execution.validate({}) == SPEC["defaults"]
    assert execution.validate() is not execution.DEFAULTS


def test_schema_rows_are_the_fixtures_in_order():
    assert [row["key"] for row in execution.SCHEMA] == [row["key"] for row in SPEC["schema"]]
    for ours, theirs in zip(execution.SCHEMA, SPEC["schema"]):
        assert ours["type"] == theirs["type"]
        assert ours["default"] == theirs["default"]
        if theirs["type"] == "integer":
            assert ours["min"] == theirs["min"]
            assert ours["max"] == theirs.get("max")
        if theirs["type"] == "string":
            assert list(ours["values"]) == theirs["values"]


@pytest.mark.parametrize("case", SPEC["normalization"]["cases"], ids=lambda c: c["name"])
def test_normalization_case(case):
    assert execution.normalize(case["input"]) == case["output"]


@pytest.mark.parametrize("case", SPEC["validationCases"], ids=lambda c: c["name"])
def test_validation_case_names_the_fixtures_message(case):
    with pytest.raises(ExecutionError) as raised:
        execution.validate(case["input"])
    assert str(raised.value) == case["error"]


def test_a_partial_object_takes_the_defaults_for_its_missing_keys():
    out = execution.validate({"mode": "session", "session": {"expectedInputs": 5}})
    assert out["mode"] == "session"
    assert out["session"] == {"expectedInputs": 5, "keepOpen": False, "renew": False, "maxRenewals": 0}
    assert out["esr"] == {"expectedCommits": 0}


def test_none_is_not_a_value():
    with pytest.raises(ExecutionError, match=r"execution\.durationHours must be an integer between 1 and 65535"):
        execution.validate({"durationHours": None})
    with pytest.raises(ExecutionError, match=r'execution\.mode must be "task" or "session"'):
        execution.validate({"mode": None})


def test_a_boolean_is_not_an_integer_and_a_number_is_not_a_boolean():
    with pytest.raises(ExecutionError, match=r"execution\.parallelism must be an integer >= 1"):
        execution.validate({"parallelism": True})
    with pytest.raises(ExecutionError, match=r"execution\.session\.keepOpen must be true or false"):
        execution.validate({"session": {"keepOpen": 1}})


def test_a_json_float_without_a_fraction_is_its_integer():
    assert execution.validate({"durationHours": 2.0})["durationHours"] == 2


def test_an_unknown_nested_key_and_a_block_that_is_not_an_object():
    with pytest.raises(ExecutionError, match=r'execution\.session has an unknown key "keepopen"'):
        execution.validate({"session": {"keepopen": True}})
    with pytest.raises(ExecutionError, match=r"execution\.esr must be an object"):
        execution.validate({"esr": 3})


def test_renew_with_renewals_is_accepted_and_renew_alone_is_refused():
    out = execution.validate({"session": {"renew": True, "maxRenewals": 2}})
    assert out["session"]["renew"] is True and out["session"]["maxRenewals"] == 2
    with pytest.raises(ExecutionError) as raised:
        execution.validate({"session": {"renew": True}})
    assert str(raised.value) == execution.RENEW_MESSAGE


def test_checks_run_in_schema_order():
    with pytest.raises(ExecutionError, match=r"execution\.durationHours"):
        execution.validate({"esr": {"expectedCommits": 101}, "durationHours": 0})
    with pytest.raises(ExecutionError, match=r"execution\.session\.maxRenewals must be >= 1"):
        execution.validate({"esr": {"expectedCommits": 101}, "session": {"renew": True}})


def test_the_shape_is_checked_before_the_values_as_the_js_sdk_checks_it():
    # execution.js validate(): unknown top-level keys, then the session and esr
    # blocks (an object, no unknown keys), then the rows in schema order.
    with pytest.raises(ExecutionError) as raised:
        execution.validate({"durationHours": 0, "session": {"x": 1}})
    assert str(raised.value) == 'execution.session has an unknown key "x"'
    with pytest.raises(ExecutionError) as raised:
        execution.validate({"mode": "batch", "esr": 3})
    assert str(raised.value) == "execution.esr must be an object"
    with pytest.raises(ExecutionError) as raised:
        execution.validate({"mode": "batch", "typo": 1, "esr": 3})
    assert str(raised.value) == 'execution has an unknown key "typo"'
    with pytest.raises(ExecutionError) as raised:
        execution.validate({"session": {"x": 1}, "esr": 3})
    assert str(raised.value) == 'execution.session has an unknown key "x"'


def test_env_keys_are_the_ten_exec_variables_in_schema_order():
    assert [env for env, _ in execution.ENV_KEYS] == [
        "EXEC_MODE", "EXEC_DURATION_HOURS", "EXEC_PARALLELISM", "EXEC_MAX_CU",
        "EXEC_HEADROOM_PERCENT", "EXEC_SESSION_EXPECTED_INPUTS", "EXEC_SESSION_KEEP_OPEN",
        "EXEC_SESSION_RENEW", "EXEC_SESSION_MAX_RENEWALS", "EXEC_ESR_EXPECTED_COMMITS"]
    assert [key for _, key in execution.ENV_KEYS] == [row["key"] for row in SPEC["schema"]]


def test_from_env_parses_strings_into_numbers_and_booleans_before_validation():
    parsed = execution.from_env({
        "EXEC_MODE": "session", "EXEC_DURATION_HOURS": "3", "EXEC_PARALLELISM": " 2 ",
        "EXEC_MAX_CU": "1500", "EXEC_HEADROOM_PERCENT": "10",
        "EXEC_SESSION_EXPECTED_INPUTS": "5", "EXEC_SESSION_KEEP_OPEN": "true",
        "EXEC_SESSION_RENEW": "1", "EXEC_SESSION_MAX_RENEWALS": "2",
        "EXEC_ESR_EXPECTED_COMMITS": "4", "OTHER": "ignored"})
    assert parsed == {
        "mode": "session", "durationHours": 3, "parallelism": 2, "maxCu": 1500, "headroomPercent": 10,
        "session": {"expectedInputs": 5, "keepOpen": True, "renew": True, "maxRenewals": 2},
        "esr": {"expectedCommits": 4}}
    assert execution.validate(parsed) == parsed


def test_from_env_leaves_an_unset_or_empty_variable_to_its_default():
    assert execution.from_env({}) == {}
    assert execution.from_env({"EXEC_MODE": "", "EXEC_DURATION_HOURS": "  "}) == {}
    assert execution.validate(execution.from_env({"EXEC_DURATION_HOURS": "4"}))["durationHours"] == 4


def test_from_env_strings_outside_the_type_fail_validation_with_the_fixtures_message():
    with pytest.raises(ExecutionError, match=r"execution\.durationHours must be an integer between 1 and 65535"):
        execution.validate(execution.from_env({"EXEC_DURATION_HOURS": "1.5"}))
    with pytest.raises(ExecutionError, match=r"execution\.durationHours must be an integer between 1 and 65535"):
        execution.validate(execution.from_env({"EXEC_DURATION_HOURS": "two"}))
    with pytest.raises(ExecutionError, match=r"execution\.session\.renew must be true or false"):
        execution.validate(execution.from_env({"EXEC_SESSION_RENEW": "maybe"}))
    with pytest.raises(ExecutionError, match=r'execution\.mode must be "task" or "session"'):
        execution.validate(execution.from_env({"EXEC_MODE": "batch"}))


@pytest.mark.parametrize("text", ["1e3", "5.", ".5", "0x10", "1_000"])
def test_a_numeric_string_outside_the_decimal_grammar_is_refused_as_the_js_sdk_refuses_it(text):
    # execution.js parseNumber: /^[+-]?\d+(\.\d+)?$/, no exponent, no bare dot.
    assert execution.parse_value("durationHours", text) == text
    with pytest.raises(ExecutionError, match=r"execution\.durationHours must be an integer between 1 and 65535"):
        execution.validate(execution.from_env({"EXEC_DURATION_HOURS": text}))


def test_a_decimal_string_with_a_fraction_is_a_float_and_a_signed_one_an_int():
    assert execution.parse_value("durationHours", "2.0") == 2.0
    assert execution.parse_value("durationHours", "+3") == 3
    assert execution.parse_value("maxCu", "-1") == -1
    assert execution.validate(execution.from_env({"EXEC_DURATION_HOURS": "2.0"}))["durationHours"] == 2


def test_to_env_validates_and_names_a_block_outside_the_schema():
    with pytest.raises(ExecutionError, match=r"execution\.esr must be an object"):
        execution.to_env({"mode": "session", "esr": 3})
    with pytest.raises(ExecutionError, match=r'execution\.mode must be "task" or "session"'):
        execution.to_env({"mode": "batch"})
    assert execution.to_env({"durationHours": 2})["EXEC_DURATION_HOURS"] == "2"


def test_to_env_writes_every_variable_as_a_string_and_round_trips():
    env = execution.to_env(execution.DEFAULTS)
    assert list(env) == [key for key, _ in execution.ENV_KEYS]
    assert env == {
        "EXEC_MODE": "task", "EXEC_DURATION_HOURS": "1", "EXEC_PARALLELISM": "1", "EXEC_MAX_CU": "0",
        "EXEC_HEADROOM_PERCENT": "25", "EXEC_SESSION_EXPECTED_INPUTS": "1", "EXEC_SESSION_KEEP_OPEN": "false",
        "EXEC_SESSION_RENEW": "false", "EXEC_SESSION_MAX_RENEWALS": "0", "EXEC_ESR_EXPECTED_COMMITS": "0"}
    full = execution.validate({"mode": "session", "session": {"expectedInputs": 7, "keepOpen": True}})
    assert execution.validate(execution.from_env(execution.to_env(full))) == full


def test_parse_block_parses_the_config_blocks_strings_and_keeps_unknown_keys():
    parsed = execution.parse_block({"durationHours": "2", "session": {"keepOpen": "yes", "typo": "x"}, "foo": "1"})
    assert parsed == {"durationHours": 2, "session": {"keepOpen": True, "typo": "x"}, "foo": "1"}
    assert execution.parse_block(None) == {}
    with pytest.raises(ExecutionError, match=r'execution has an unknown key "foo"'):
        execution.validate(parsed)


def test_complete_fills_the_defaults_and_keeps_what_was_stored():
    out = execution.complete({"durationHours": 3, "session": {"expectedInputs": 2}, "extra": 1})
    assert out["durationHours"] == 3 and out["session"]["expectedInputs"] == 2
    assert out["session"]["keepOpen"] is False and out["esr"] == {"expectedCommits": 0}
    assert out["extra"] == 1
    assert execution.complete({}) == execution.DEFAULTS


def test_normalize_keeps_the_flat_keys_and_drops_duration_and_execution():
    out = execution.normalize({"taskPrice": 2.5, "cpu": 1, "duration": 3})
    assert out["resources"] == {"taskPrice": 2.5, "cpu": 1}
    assert out["execution"]["durationHours"] == 3
    with pytest.raises(ExecutionError, match=r"execution\.durationHours"):
        execution.normalize({"duration": 0})


def test_normalize_treats_a_non_object_resources_as_empty_and_an_execution_set_to_none_as_an_error():
    # execution.js normalize(): a resources that is not an object is {}; an
    # execution key set to null is not an absent one.
    assert execution.normalize(None) == {"resources": {}, "execution": execution.DEFAULTS}
    assert execution.normalize("x") == {"resources": {}, "execution": execution.DEFAULTS}
    with pytest.raises(ExecutionError, match=r"^execution must be an object$"):
        execution.normalize({"duration": 2, "execution": None})
    with pytest.raises(ExecutionError, match=r"^execution must be an object$"):
        execution.normalize({"execution": "x"})


def test_relays_per_hour_is_the_rows_plus_ten_with_esr():
    assert execution.ESR_RELAYS_PER_HOUR == 10
    assert execution.relays_per_hour(execution.DEFAULTS, False) == 2
    assert execution.relays_per_hour(execution.DEFAULTS, True) == 12
    session = {"mode": "session", "session": {"expectedInputs": 5}}
    assert execution.relays_per_hour(session, False) == 6
    assert execution.relays_per_hour(session, True) == 16
