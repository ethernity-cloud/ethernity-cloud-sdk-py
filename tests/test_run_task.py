"""ecld-run's task: --input / --input-text reach the runner as the task's input
dataset, and a task the enclave reports with a non-zero task code prints its
error report and exits 1. No chain and no network: the runner is replaced."""
import pytest

from ethernity_cloud_sdk_py.commands import run

KEY = "0x" + "44" * 32


class _Runner:
    """Records run()'s arguments and answers with the configured outcome."""
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
        _Runner.run_kwargs = None
        return run.main(["--network", "BLOXBERG_TESTNET", "--securelock", "demo", *argv, "f()"])
    return ecld_run


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
