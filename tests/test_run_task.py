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
    assert ecld_run("--mode", "session", "--parallelism", "2", "--max-gflops", "150", "--headroom", "10",
                    "--expected-inputs", "5", "--keep-open", "--renew", "--max-renewals", "2",
                    "--expected-commits", "4") == 0
    assert _resources()["execution"] == {
        "mode": "session", "durationHours": 1, "parallelism": 2, "maxGflops": 150, "headroomPercent": 10,
        "session": {"expectedInputs": 5, "keepOpen": True, "renew": True, "maxRenewals": 2},
        "esr": {"expectedCommits": 4}}


def test_max_gflops_sets_the_node_cap_in_gflops(ecld_run, monkeypatch):
    assert ecld_run("--max-gflops", "150") == 0
    assert _resources()["execution"]["maxGflops"] == 150
    monkeypatch.setenv("EXEC_MAX_GFLOPS", "200")
    assert ecld_run() == 0
    assert _resources()["execution"]["maxGflops"] == 200


def test_an_unknown_option_is_a_usage_error_and_never_reaches_the_payload(ecld_run, capsys):
    with pytest.raises(SystemExit) as raised:
        ecld_run("--max-cu", "150")
    assert raised.value.code == 2
    assert "unrecognized arguments: --max-cu" in capsys.readouterr().err
    assert _Runner.run_args is None


def test_an_exec_variable_outside_the_execution_variables_is_a_usage_error_naming_it(ecld_run, monkeypatch, capsys):
    monkeypatch.setenv("EXEC_MAX_CU", "1500")
    with pytest.raises(SystemExit) as raised:
        ecld_run()
    assert raised.value.code == 2
    assert 'execution has an unknown variable "EXEC_MAX_CU" (the execution variables: %s)' % (
        ", ".join(env for env, _ in execution.ENV_KEYS)) in capsys.readouterr().err
    assert _Runner.run_args is None


def test_a_stored_max_cu_is_a_usage_error_as_an_unknown_key(ecld_run, tmp_path, capsys):
    (tmp_path / ".config.json").write_text(json.dumps({"EXECUTION": {"maxCu": 1500}}), encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        ecld_run()
    assert raised.value.code == 2
    assert 'execution has an unknown key "maxCu"' in capsys.readouterr().err
    assert _Runner.run_args is None


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


# The claims settings of the resources (ethernity-cloud-runner-py run()).
CLAIMS_KEYS = {"max_price_per_cu_hour_wei", "max_bid_per_cu_hour_wei", "escrow_wei", "required_gflops",
               "gas_level", "epc_mib", "max_gflops", "relays_per_hour", "min_node_score", "allow_marked_image"}


def _claims():
    return {key: value for key, value in _resources().items() if key in CLAIMS_KEYS}


def test_without_a_claims_flag_the_resources_carry_no_claims_setting(ecld_run):
    assert ecld_run("--max-gflops", "150", "--relays-per-hour", "3") == 0
    assert _claims() == {}


def test_bid_wei_adds_the_v2_terms_with_the_gflops_ceiling_and_the_relays_and_each_flag_reaches_its_key(ecld_run):
    assert ecld_run("--bid-wei", "1200000000000000000") == 0
    assert _claims() == {"max_price_per_cu_hour_wei": 1200000000000000000, "max_gflops": 0, "relays_per_hour": 2}
    assert ecld_run("--bid-wei", str(2**128 - 1), "--bid-ceiling-wei", "2000000000000000000",
                    "--escrow-wei", "5000000000000000000", "--required-gflops", "250",
                    "--gas-level", "2000000000000000000", "--epc-mib", "256", "--max-gflops", "400",
                    "--relays-per-hour", "7", "--min-node-score", "0.5", "--allow-marked-image") == 0
    assert _claims() == {
        "max_price_per_cu_hour_wei": 2**128 - 1, "max_bid_per_cu_hour_wei": 2000000000000000000,
        "escrow_wei": 5000000000000000000, "required_gflops": 250, "gas_level": 2000000000000000000,
        "epc_mib": 256, "max_gflops": 400, "relays_per_hour": 7, "min_node_score": 0.5, "allow_marked_image": True}
    assert ecld_run("--bid-wei", "1", "--expected-inputs", "5") == 0
    assert _claims()["relays_per_hour"] == 6
    assert ecld_run("--min-node-score", "0") == 0
    assert _claims() == {"min_node_score": 0.0}


@pytest.mark.parametrize("args", [
    ("--bid-wei", "0"), ("--bid-wei", "1.5"), ("--bid-wei", str(2**128)), ("--bid-wei", "-1"),
    ("--bid-wei", "1", "--bid-ceiling-wei", "0"), ("--bid-wei", "1", "--escrow-wei", "0"),
    ("--bid-wei", "1", "--gas-level", str(10**18 - 1)), ("--bid-wei", "1", "--required-gflops", "65536"),
    ("--bid-wei", "1", "--epc-mib", "0"), ("--bid-wei", "1", "--epc-mib", "4194241"),
    ("--min-node-score", "5.01"), ("--min-node-score", "-1"), ("--min-node-score", "x"), ("--min-node-score", ".5"),
])
def test_a_claims_flag_outside_the_runners_bounds_is_a_usage_error(ecld_run, args, capsys):
    with pytest.raises(SystemExit) as raised:
        ecld_run(*args)
    assert raised.value.code == 2
    assert args[-2] in capsys.readouterr().err
    assert _Runner.run_args is None


def test_the_bounds_admit_their_ends(ecld_run):
    assert ecld_run("--bid-wei", "1", "--epc-mib", "4194240", "--gas-level", str(10**18),
                    "--required-gflops", "65535") == 0
    assert (_claims()["epc_mib"], _claims()["gas_level"], _claims()["required_gflops"]) == (4194240, 10**18, 65535)


@pytest.mark.parametrize("flag", ["--bid-ceiling-wei", "--escrow-wei", "--gas-level", "--required-gflops",
                                  "--epc-mib"])
def test_a_v2_term_without_bid_wei_is_a_usage_error(ecld_run, flag, capsys):
    with pytest.raises(SystemExit) as raised:
        ecld_run(flag, str(10**18) if flag == "--gas-level" else "64")
    assert raised.value.code == 2
    assert (f"{flag} states a V2 term of the request, which is added with V2 terms only with --bid-wei"
            in capsys.readouterr().err)
    assert _Runner.run_args is None


def test_min_node_score_and_allow_marked_image_stand_alone(ecld_run):
    assert ecld_run("--allow-marked-image") == 0
    assert _claims() == {"allow_marked_image": True}


REFUSAL = ("This network serves DO requests by claims (PoxMatching's claimsOn() is true), so a request is added "
           "with V2 terms: give the task setting max_price_per_cu_hour_wei (the bid per CU-hour, wei), above 0; "
           "escrow_wei, when given, is the most addDORequestV2 may pull, above 0.")


def test_the_runners_refusal_of_a_request_without_a_bid_ends_ecld_run_with_exit_1_and_its_message(ecld_run, capsys):
    assert ecld_run(outcome=({"status": "ERROR", "progress": "INIT", "last_error": REFUSAL}, None)) == 1
    err = capsys.readouterr().err
    assert "status    : ERROR" in err
    assert f"error     : {REFUSAL}" in err
