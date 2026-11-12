"""ecld-publish's EXECUTION gate: a block outside the schema ends the run with
the schema's message (exit 2) before the key is read and before anything is
published; a block inside it passes the gate. ecld-publish runs in a process
of its own, as test_testnet_cas.py runs it (publish.py reads .config.json
from the process's CWD at import); unattended, without a key, so a run that
passes the gate stops at the key with the ECLD_PRIVATE_KEY message. No chain
is reached."""
import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ecld_publish(tmp_path, block):
    (tmp_path / ".config.json").write_text(json.dumps({
        "PROJECT_NAME": "demo", "BLOCKCHAIN_NETWORK": "BLOXBERG_TESTNET", "DAPP_TYPE": "Pynithy",
        "VERSION": "v3", "EXECUTION": block}), encoding="utf-8")
    env = {name: value for name, value in os.environ.items() if not name.startswith("ECLD_")}
    env.update(PYTHONPATH=REPO_ROOT, PYTHONIOENCODING="utf-8", ECLD_NON_INTERACTIVE="1")
    return subprocess.run(
        [sys.executable, "-c", "from ethernity_cloud_sdk_py.cli import main_publish; main_publish()"],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8")


@pytest.mark.parametrize("block, message", [
    ("x", "execution must be an object"),
    ({"mode": "batch", "session": 3}, "execution.session must be an object"),
    ({"durationHours": 0}, "execution.durationHours must be an integer between 1 and 65535"),
    ({"paralelism": 2}, 'execution has an unknown key "paralelism"'),
])
def test_a_block_outside_the_schema_ends_the_publish_before_the_key(tmp_path, block, message):
    run = _ecld_publish(tmp_path, block)
    assert run.returncode == 2, run.stdout + run.stderr
    assert f"ERROR: {message}" in run.stdout
    assert "ECLD_PRIVATE_KEY" not in run.stdout


def test_a_block_inside_the_schema_passes_the_gate(tmp_path):
    run = _ecld_publish(tmp_path, {"mode": "session", "session": {"expectedInputs": "4"}})
    assert run.returncode == 2, run.stdout + run.stderr
    assert "ECLD_PRIVATE_KEY" in run.stdout
    assert "ERROR: execution" not in run.stdout
