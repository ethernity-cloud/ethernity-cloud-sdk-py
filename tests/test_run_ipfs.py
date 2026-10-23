"""ecld-run's storage: the payload intake by default, a Kubo RPC API of the
user's own with --ipfs, handed to the runner ending in /api/v0 and carrying
--ipfs-token / ECLD_IPFS_TOKEN as its Authorization header. No chain and no
network: the runner is replaced."""
import pytest

from ethernity_cloud_sdk_py.commands import run

KEY = "0x" + "44" * 32
API = "http://127.0.0.1:5001/api/v0"


class _Runner:
    """Records the storage call and answers run() with a SUCCESS result."""
    calls = []
    order_id = 7
    do_request_id = 9

    def __init__(self, network_name, network_type):
        pass

    def set_log_level(self, level):
        pass

    def set_private_key(self, key):
        pass

    def set_public_intake(self):
        _Runner.calls.append(("intake",))

    def set_storage_ipfs(self, url, token=""):
        _Runner.calls.append(("ipfs", url, token))

    def connect(self):
        pass

    def run(self, *args, **kwargs):
        pass

    def is_running(self):
        return False

    def get_state(self):
        return {"status": "SUCCESS", "progress": "done"}

    def get_result(self):
        return {"value": "ok", "result_task_code": "SUCCESS", "result_task_code_int": 0}


@pytest.fixture
def ecld_run(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECLD_PRIVATE_KEY", KEY)
    monkeypatch.delenv("ECLD_IPFS_TOKEN", raising=False)
    monkeypatch.setattr("ethernity_cloud_runner_py.runner.EthernityCloudRunner", _Runner)
    _Runner.calls = []
    return lambda *argv: run.main(["--network", "BLOXBERG_TESTNET", "--securelock", "demo", *argv, "hello()"])


def test_the_default_delivers_through_the_payload_intake(ecld_run):
    assert ecld_run() == 0
    assert _Runner.calls == [("intake",)]


def test_an_ipfs_api_takes_the_token_flag_as_its_header(ecld_run):
    assert ecld_run("--ipfs", "http://127.0.0.1:5001", "--ipfs-token", "writer-token") == 0
    assert _Runner.calls == [("ipfs", API, "writer-token")]


def test_the_token_defaults_to_the_environment(ecld_run, monkeypatch):
    monkeypatch.setenv("ECLD_IPFS_TOKEN", "env-token")
    assert ecld_run("--ipfs", "http://127.0.0.1:5001") == 0
    assert _Runner.calls == [("ipfs", API, "env-token")]


def test_an_own_node_needs_no_token(ecld_run):
    assert ecld_run("--ipfs", "http://127.0.0.1:5001") == 0
    assert _Runner.calls == [("ipfs", API, "")]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:5001",
    "http://127.0.0.1:5001/",
    "http://127.0.0.1:5001/api/v0",
    "http://127.0.0.1:5001/api/v0/",
])
def test_the_runner_is_handed_the_rpc_api_path(ecld_run, url):
    """The runner posts to <url>/add and <url>/cat: the URL it gets ends in
    /api/v0 whether the flag named the node or its API."""
    assert ecld_run("--ipfs", url) == 0
    assert _Runner.calls == [("ipfs", API, "")]
