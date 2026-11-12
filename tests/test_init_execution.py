"""ecld-init's execution step: the EXECUTION block it writes to .config.json
is complete (every key of tests/spec/execution-spec.json), ECLD_EXEC_* strings
are parsed into numbers and booleans, a stored block is kept, and a value
outside the schema ends the run with the schema's message. ecld-init is
driven as unattended_acceptance.sh drives it, in a process of its own (init.py
reads .config.json from the process's CWD); no chain is reached."""
import json
import os
import subprocess
import sys

from ethernity_cloud_sdk_py.commands import execution

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ecld_init(tmp_path, **exec_env):
    env = {name: value for name, value in os.environ.items() if not name.startswith("ECLD_")}
    env.update(PYTHONPATH=REPO_ROOT, PYTHONIOENCODING="utf-8",
               ECLD_NON_INTERACTIVE="1", ECLD_PROJECT_NAME="demo",
               ECLD_BLOCKCHAIN_NETWORK="BLOXBERG_TESTNET", ECLD_DAPP_TYPE="Pynithy",
               ECLD_APP_TEMPLATE="no", **exec_env)
    return subprocess.run(
        [sys.executable, "-c", "from ethernity_cloud_sdk_py.cli import main_init; main_init()"],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8")


def _config(tmp_path):
    return json.loads((tmp_path / ".config.json").read_text(encoding="utf-8"))


def test_an_unattended_init_writes_the_complete_default_block(tmp_path):
    assert _ecld_init(tmp_path).returncode == 0
    assert _config(tmp_path)["EXECUTION"] == execution.DEFAULTS


def test_ecld_exec_variables_are_parsed_and_written(tmp_path):
    run = _ecld_init(tmp_path, ECLD_EXEC_MODE="session", ECLD_EXEC_DURATION_HOURS="3",
                     ECLD_EXEC_PARALLELISM="2", ECLD_EXEC_MAX_CU="1500", ECLD_EXEC_HEADROOM_PERCENT="10",
                     ECLD_EXEC_SESSION_EXPECTED_INPUTS="5", ECLD_EXEC_SESSION_KEEP_OPEN="true",
                     ECLD_EXEC_SESSION_RENEW="yes", ECLD_EXEC_SESSION_MAX_RENEWALS="2",
                     ECLD_EXEC_ESR_EXPECTED_COMMITS="4")
    assert run.returncode == 0, run.stdout + run.stderr
    assert _config(tmp_path)["EXECUTION"] == {
        "mode": "session", "durationHours": 3, "parallelism": 2, "maxCu": 1500, "headroomPercent": 10,
        "session": {"expectedInputs": 5, "keepOpen": True, "renew": True, "maxRenewals": 2},
        "esr": {"expectedCommits": 4}}
    assert "durationHours [ECLD_EXEC_DURATION_HOURS -> 3]" in run.stdout


def test_a_stored_block_is_kept_and_an_unset_key_keeps_its_value(tmp_path):
    (tmp_path / ".config.json").write_text(json.dumps({
        "EXECUTION": {"durationHours": 6, "session": {"expectedInputs": 3}}}), encoding="utf-8")
    assert _ecld_init(tmp_path, ECLD_EXEC_PARALLELISM="2").returncode == 0
    block = _config(tmp_path)["EXECUTION"]
    assert block["durationHours"] == 6 and block["parallelism"] == 2
    assert block["session"] == {"expectedInputs": 3, "keepOpen": False, "renew": False, "maxRenewals": 0}


def test_a_value_outside_the_schema_ends_the_run_with_the_schemas_message(tmp_path):
    run = _ecld_init(tmp_path, ECLD_EXEC_DURATION_HOURS="0")
    assert run.returncode == 2
    assert "ERROR: execution.durationHours must be an integer between 1 and 65535" in run.stdout
    run = _ecld_init(tmp_path, ECLD_EXEC_MODE="session", ECLD_EXEC_SESSION_RENEW="true")
    assert run.returncode == 2
    assert "ERROR: " + execution.RENEW_MESSAGE in run.stdout


def test_a_stored_block_that_is_not_an_object_ends_the_run_with_the_schemas_message(tmp_path):
    (tmp_path / ".config.json").write_text(json.dumps({"EXECUTION": "x"}), encoding="utf-8")
    run = _ecld_init(tmp_path)
    assert run.returncode == 2, run.stdout + run.stderr
    assert "ERROR: execution must be an object" in run.stdout
    (tmp_path / ".config.json").write_text(json.dumps({"EXECUTION": {"session": 3}}), encoding="utf-8")
    run = _ecld_init(tmp_path, ECLD_EXEC_SESSION_KEEP_OPEN="true")
    assert run.returncode == 2, run.stdout + run.stderr
    assert "ERROR: execution.session must be an object" in run.stdout
