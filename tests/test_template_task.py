"""The task script ecld-init writes, templates/src/ethernity_task.py, driven
against a runner that answers as ethernity-cloud-runner-py's does: run()
returns at once, is_running() holds while the task's thread works, and
get_state() returns status, progress, last_error, result, processed_events and
remaining_events. execute_task waits for the task, printing each step it
reaches, and prints the result or the error. No chain, no key file and no
.env: the key manager, the password prompt and the runner are replaced."""
import importlib.util
import os

import pytest

import ethernity_cloud_sdk_py

TEMPLATE = os.path.join(os.path.dirname(ethernity_cloud_sdk_py.__file__), "templates", "src", "ethernity_task.py")
KEY = "0x" + "44" * 32
STEPS = ["INIT", "CREATED", "ORDER_PLACED", "IN_PROGRESS", "FINISHED"]


class _Runner:
    """A runner whose task walks STEPS, one step per get_state() call while it runs, and ends with `outcome`:
    (status, last_error, result)."""
    outcome = ("SUCCESS", None, {"value": "Hello, World!"})
    built = []

    def __init__(self, network_name, network_type):
        self.network = (network_name, network_type)
        self.step = 0
        self.ran = None
        self.storage = None
        _Runner.built.append(self)

    def set_log_level(self, level):
        pass

    def set_private_key(self, key):
        self.key = key

    def set_public_intake(self):
        self.storage = "intake"

    def connect(self):
        pass

    def run(self, *args, **kwargs):
        self.ran = (args, kwargs)

    def is_running(self):
        return self.ran is not None and self.step < len(STEPS)

    def get_state(self):
        if self.is_running():
            progress = STEPS[self.step]
            self.step += 1
            return {"status": "RUNNING", "progress": progress, "last_error": None, "result": None,
                    "processed_events": STEPS[:self.step - 1], "remaining_events": STEPS[self.step - 1:]}
        status, last_error, result = _Runner.outcome
        return {"status": status, "progress": "FINISHED", "last_error": last_error, "result": result,
                "processed_events": STEPS if status == "SUCCESS" else STEPS[:1],
                "remaining_events": [] if status == "SUCCESS" else STEPS[1:]}

    def get_result(self):
        return _Runner.outcome[2]


class _Keys:
    def __init__(self, password):
        assert password == "the password"

    def decrypt_private_key(self, encrypted):
        assert encrypted == "the encrypted key"
        return KEY


@pytest.fixture
def template(monkeypatch):
    """The template module, its runner, key manager and prompt replaced, its waits taking no time, and
    python-dotenv disabled."""
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("ENC_PRIVATE_KEY", "the encrypted key")
    monkeypatch.setenv("NETWORK_NAME", "BLOXBERG")
    monkeypatch.setenv("NETWORK_TYPE", "TESTNET")
    monkeypatch.setenv("PROJECT_NAME", "my-dapp")
    monkeypatch.setenv("TRUSTED_ZONE_IMAGE", "etny-pynithy-testnet")
    for name in [name for name in os.environ if name.startswith("EXEC_")]:
        monkeypatch.delenv(name)
    spec = importlib.util.spec_from_file_location("ethernity_task_template", TEMPLATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "EthernityCloudRunner", _Runner)
    monkeypatch.setattr(module, "PrivateKeyManager", _Keys)
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt="": "the password")
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    _Runner.built = []
    return module


def test_the_template_waits_for_the_task_and_prints_each_step_and_the_result(template, capsys):
    template.execute_task(template.code)
    out = capsys.readouterr().out
    assert out.splitlines() == [f"[{step}] RUNNING" for step in STEPS] + ["Hello, World!"]
    runner, = _Runner.built
    assert runner.network == ("BLOXBERG", "TESTNET") and runner.key == KEY and runner.storage == "intake"
    args, kwargs = runner.ran
    assert args[1:] == ("my-dapp", "v3", "hello('World!')", "", "etny-pynithy-testnet")
    assert runner.is_running() is False


def test_the_template_prints_the_error_and_the_report_of_a_task_that_failed(template, capsys, monkeypatch):
    monkeypatch.setattr(_Runner, "outcome", (
        "ERROR", "The task failed in the enclave with BASE_EXCEPTION (4)",
        {"value": "BASE_EXCEPTION: RuntimeError('boom')"}))
    template.execute_task(template.code)
    lines = capsys.readouterr().out.splitlines()
    assert lines[:len(STEPS)] == [f"[{step}] RUNNING" for step in STEPS]
    assert lines[len(STEPS):] == [
        "Error: The task failed in the enclave with BASE_EXCEPTION (4)",
        "BASE_EXCEPTION: RuntimeError('boom')",
        f"Processed Events: ['INIT'], Remaining Events: {STEPS[1:]}",
    ]
