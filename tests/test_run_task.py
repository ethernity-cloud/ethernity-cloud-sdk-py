"""ecld-run's task: --input / --input-text reach the runner as the task's input
dataset, a task the enclave reports with a non-zero task code prints its
error report and exits 1, --task-price takes a fractional price, and the
execution object reaches the runner nested in the resources: the EXECUTION
block of .config.json, the EXEC_* variables over it, the flags over both. No
chain and no network: the runner is replaced."""
import json
from decimal import Decimal

import pytest

from ethernity_cloud_sdk_py.commands import execution, run

KEY = "0x" + "44" * 32


class _Runner:
    """Records run()'s arguments and answers with the configured outcome."""
    run_args = None
    run_kwargs = None
    state = None
    result = None
    order_id = 7
    do_request_id = 9

    def __init__(self, network_name, network_type):
        pass

    def set_log_level(self, level):
        pass

    def set_private_key(self, key):
        pass

    def set_public_intake(self):
        pass

    def connect(self):
        pass

    def run(self, *args, **kwargs):
        _Runner.run_args = args
        _Runner.run_kwargs = kwargs

    def is_running(self):
        return False

    def get_state(self):
        return _Runner.state

    def get_result(self):
        return _Runner.result


SUCCESS = ({"status": "SUCCESS", "progress": "FINISHED", "last_error": None},
           {"value": "ok", "result_task_code": "SUCCESS", "result_task_code_int": 0})
RAISED = ({"status": "ERROR", "progress": "FINISHED",
           "last_error": "The task failed in the enclave with BASE_EXCEPTION (4)"},
          {"value": "BASE_EXCEPTION: RuntimeError('boom')",
           "result_task_code": "BASE_EXCEPTION", "result_task_code_int": 4})


@pytest.fixture
def ecld_run(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECLD_PRIVATE_KEY", KEY)
    monkeypatch.setattr("ethernity_cloud_runner_py.runner.EthernityCloudRunner", _Runner)

    def ecld_run(*argv, outcome=SUCCESS):
        _Runner.state, _Runner.result = outcome
        _Runner.run_args = _Runner.run_kwargs = None
        return run.main(["--network", "BLOXBERG_TESTNET", "--securelock", "demo", *argv, "f()"])
    return ecld_run


def _resources():
    return _Runner.run_args[0]


def test_input_text_is_the_input_dataset(ecld_run):
    assert ecld_run("--input-text", "a,b,c") == 0
    assert _Runner.run_kwargs["input_data"] == "a,b,c"


def test_an_input_file_is_the_input_dataset(ecld_run, tmp_path):
    (tmp_path / "data.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    assert ecld_run("--input", "data.csv") == 0
    assert _Runner.run_kwargs["input_data"] == "x,y\n1,2\n"


def test_no_input_flag_sends_no_input(ecld_run):
    assert ecld_run() == 0
    assert _Runner.run_kwargs["input_data"] is None


def test_a_task_that_raised_prints_its_report_and_exits_1(ecld_run, capsys):
    assert ecld_run(outcome=RAISED) == 1
    out, err = capsys.readouterr()
    assert "task code : 4 (BASE_EXCEPTION)" in out
    assert "RuntimeError('boom')" in out
    assert "BASE_EXCEPTION (4)" in err


def test_a_successful_task_exits_0_without_a_task_code_line(ecld_run, capsys):
    assert ecld_run() == 0
    assert "task code" not in capsys.readouterr().out


def test_a_whole_task_price_is_passed_as_an_int(ecld_run):
    assert ecld_run("--task-price", "3") == 0
    assert _resources()["taskPrice"] == 3 and type(_resources()["taskPrice"]) is int


def test_a_fractional_task_price_is_passed_as_the_decimal_given(ecld_run):
    assert ecld_run("--task-price", "2.5") == 0
    assert _resources()["taskPrice"] == Decimal("2.5")
    assert isinstance(_resources()["taskPrice"], Decimal)


@pytest.mark.parametrize("price", ["x", "-1", "NaN", "inf"])
def test_a_task_price_that_is_not_a_non_negative_number_is_a_usage_error(ecld_run, price, capsys):
    with pytest.raises(SystemExit) as raised:
        ecld_run("--task-price", price)
    assert raised.value.code == 2
    assert "--task-price" in capsys.readouterr().err
    assert _Runner.run_args is None


def test_parse_args_is_exported_and_duration_maps_to_duration_hours():
    args = run.build_parser().parse_args(["--duration", "4", "f()"])
    assert args.duration_hours == "4"
    assert run.build_parser().parse_args(["--duration-hours", "5", "f()"]).duration_hours == "5"


def test_the_default_execution_reaches_the_runner_nested_beside_the_flat_duration(ecld_run):
    assert ecld_run() == 0
    assert _resources()["execution"] == execution.DEFAULTS
    assert _resources()["duration"] == 1


def test_duration_sets_execution_duration_hours_and_the_flat_duration(ecld_run):
    assert ecld_run("--duration", "4") == 0
    assert _resources()["execution"]["durationHours"] == 4
    assert _resources()["duration"] == 4


def test_the_config_block_is_read_and_the_flags_override_it(ecld_run, tmp_path):
    (tmp_path / ".config.json").write_text(json.dumps({
        "EXECUTION": {"mode": "session", "durationHours": 3, "session": {"expectedInputs": 4}}}), encoding="utf-8")
    assert ecld_run() == 0
    assert _resources()["execution"]["mode"] == "session"
    assert _resources()["execution"]["session"]["expectedInputs"] == 4
    assert _resources()["duration"] == 3
    assert ecld_run("--expected-inputs", "9", "--duration-hours", "2") == 0
    assert _resources()["execution"]["session"]["expectedInputs"] == 9
    assert _resources()["execution"]["durationHours"] == 2
    assert _resources()["duration"] == 2


def test_exec_variables_override_the_config_and_the_flags_override_them(ecld_run, tmp_path, monkeypatch):
    (tmp_path / ".config.json").write_text(json.dumps({"EXECUTION": {"durationHours": 3}}), encoding="utf-8")
    monkeypatch.setenv("EXEC_DURATION_HOURS", "5")
    monkeypatch.setenv("EXEC_SESSION_KEEP_OPEN", "true")
    assert ecld_run() == 0
    assert _resources()["execution"]["durationHours"] == 5
    assert _resources()["execution"]["session"]["keepOpen"] is True
    assert ecld_run("--duration", "7", "--no-keep-open") == 0
    assert _resources()["execution"]["durationHours"] == 7
    assert _resources()["execution"]["session"]["keepOpen"] is False


def test_the_boolean_flags_take_no_value_so_a_payload_after_them_is_the_payload():
    # The JS SDK's ecld-run reads --keep-open / --no-keep-open and --renew /
    # --no-renew the same way.
    args = run.build_parser().parse_args(["--keep-open", "f()"])
    assert args.keep_open is True and args.code == ["f()"]
    args = run.build_parser().parse_args(["--no-renew", "--keep-open", "g()"])
    assert args.renew is False and args.keep_open is True and args.code == ["g()"]
    args = run.build_parser().parse_args(["h()"])
    assert args.keep_open is None and args.renew is None
    # A word after the flag is the payload, never the flag's value.
    args = run.build_parser().parse_args(["--keep-open", "false"])
    assert args.keep_open is True and args.code == ["false"]


def test_a_stored_block_that_is_not_an_object_is_named_and_not_replaced(ecld_run, tmp_path, monkeypatch, capsys):
    (tmp_path / ".config.json").write_text(json.dumps({"EXECUTION": "x"}), encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        ecld_run()
    assert raised.value.code == 2
    assert "execution must be an object" in capsys.readouterr().err
    (tmp_path / ".config.json").write_text(json.dumps({"EXECUTION": {"session": 3}}), encoding="utf-8")
    monkeypatch.setenv("EXEC_SESSION_KEEP_OPEN", "true")
    with pytest.raises(SystemExit) as raised:
        ecld_run("--renew", "--max-renewals", "2")
    assert raised.value.code == 2
    assert "execution.session must be an object" in capsys.readouterr().err
    assert _Runner.run_args is None


def test_every_execution_flag_is_parsed_and_the_boolean_flags_stand_alone(ecld_run):
    assert ecld_run("--mode", "session", "--parallelism", "2", "--max-cu", "1500", "--headroom", "10",
                    "--expected-inputs", "5", "--keep-open", "--renew", "--max-renewals", "2",
                    "--expected-commits", "4") == 0
    assert _resources()["execution"] == {
        "mode": "session", "durationHours": 1, "parallelism": 2, "maxCu": 1500, "headroomPercent": 10,
        "session": {"expectedInputs": 5, "keepOpen": True, "renew": True, "maxRenewals": 2},
        "esr": {"expectedCommits": 4}}


def test_an_execution_value_outside_the_schema_is_a_usage_error_with_the_schemas_message(ecld_run, capsys):
    with pytest.raises(SystemExit) as raised:
        ecld_run("--duration", "1.5")
    assert raised.value.code == 2
    assert "execution.durationHours must be an integer between 1 and 65535" in capsys.readouterr().err
    assert _Runner.run_args is None


def test_relays_per_hour_is_printed_from_the_execution_and_a_given_value_wins(ecld_run, capsys):
    assert ecld_run("--expected-inputs", "5") == 0
    assert "relays/h  : 6" in capsys.readouterr().out
    assert ecld_run("--relays-per-hour", "20") == 0
    assert "relays/h  : 20" in capsys.readouterr().out
