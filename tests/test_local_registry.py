"""The registry a publish serves its own build's image tree from: the
container it runs over the tree, the loopback port free on the host it
publishes it at, the tree it requires, and its removal on every exit. No
docker and no network: subprocess.run, the free port and the readiness probe
are replaced."""
import subprocess

import pytest

from ethernity_cloud_sdk_py.commands.pynithy import local_registry
from ethernity_cloud_sdk_py.commands.pynithy.local_registry import LocalRegistry

PORT = 49153
HOST = f"127.0.0.1:{PORT}"


class _Docker:
    """A subprocess.run that records every command and gives `docker run`
    the exit code `run_returncode`."""

    def __init__(self, run_returncode=0):
        self.commands = []
        self.run_returncode = run_returncode

    def __call__(self, args, **kwargs):
        self.commands.append(list(args))
        if args[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(args, self.run_returncode, stdout="", stderr="port is already allocated")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


class _Answer:
    def raise_for_status(self):
        pass


def _tree(tmp_path):
    tree = tmp_path / "build" / "registry"
    (tree / "docker" / "registry" / "v2").mkdir(parents=True)
    return str(tree)


def _registry(monkeypatch, tmp_path, docker, answers=True):
    monkeypatch.setattr(subprocess, "run", docker)
    monkeypatch.setattr(local_registry, "free_port", lambda: PORT)
    monkeypatch.setattr(local_registry.time, "sleep", lambda seconds: None)
    if answers:
        monkeypatch.setattr(local_registry.requests, "get", lambda url, timeout: _Answer())
    else:
        def refused(url, timeout):
            raise ConnectionError(url)
        monkeypatch.setattr(local_registry.requests, "get", refused)
        monkeypatch.setattr(local_registry, "READY_SECONDS", 0)
    return LocalRegistry("accept m3", _tree(tmp_path))


def test_the_container_is_named_after_the_project(tmp_path):
    assert LocalRegistry("accept m3", str(tmp_path)).container == "ecld-registry-accept-m3"


def test_start_serves_the_tree_read_only_on_a_loopback_port_free_on_the_host(monkeypatch, tmp_path):
    docker = _Docker()
    registry = _registry(monkeypatch, tmp_path, docker)
    assert registry.start() is True
    assert registry.host == HOST
    assert registry.image("etny-securelock") == f"{HOST}/etny-securelock"
    assert docker.commands == [
        ["docker", "rm", "-f", "ecld-registry-accept-m3"],
        ["docker", "run", "-d", "--name", "ecld-registry-accept-m3", "-p", f"127.0.0.1:{PORT}:5000",
         "-v", f"{registry.tree}:/var/lib/registry:ro", "registry:2"],
    ]


def test_the_port_is_one_free_on_the_loopback_interface(monkeypatch, tmp_path):
    """With free_port in place: the port the system gave a bind of port 0 on
    127.0.0.1, published as a fixed host port."""
    docker = _Docker()
    monkeypatch.setattr(subprocess, "run", docker)
    monkeypatch.setattr(local_registry.requests, "get", lambda url, timeout: _Answer())
    registry = LocalRegistry("demo", _tree(tmp_path))
    registry.start()
    address, port = registry.host.rsplit(":", 1)
    assert address == "127.0.0.1" and 0 < int(port) < 65536
    assert docker.commands[1][5:7] == ["-p", f"127.0.0.1:{port}:5000"]


def test_a_directory_that_is_no_build_tree_is_refused_before_docker_runs(monkeypatch, tmp_path):
    docker = _Docker()
    monkeypatch.setattr(subprocess, "run", docker)
    registry = LocalRegistry("demo", str(tmp_path / "build" / "registry"))
    with pytest.raises(Exception, match="run ecld-build first"):
        registry.start()
    assert docker.commands == []


def test_a_run_docker_refuses_leaves_no_container(monkeypatch, tmp_path):
    docker = _Docker(run_returncode=125)
    registry = _registry(monkeypatch, tmp_path, docker)
    with pytest.raises(Exception, match="docker could not start registry:2: port is already allocated"):
        registry.start()
    assert [c[:2] for c in docker.commands] == [["docker", "rm"], ["docker", "run"], ["docker", "rm"]]
    assert docker.commands[-1] == ["docker", "rm", "-f", "ecld-registry-accept-m3"]
    assert registry.host is None


def test_a_registry_that_does_not_answer_is_removed(monkeypatch, tmp_path):
    docker = _Docker()
    registry = _registry(monkeypatch, tmp_path, docker, answers=False)
    with pytest.raises(Exception, match="did not answer"):
        registry.start()
    assert docker.commands[-2:] == [
        ["docker", "rm", "-f", "ecld-registry-accept-m3"],
        ["docker", "rmi", f"{HOST}/etny-las", f"{HOST}/etny-securelock", f"{HOST}/etny-trustedzone"],
    ]


def test_stop_removes_the_container_and_the_tags_pulled_from_it(monkeypatch, tmp_path):
    docker = _Docker()
    registry = _registry(monkeypatch, tmp_path, docker)
    registry.stop()
    assert docker.commands == [["docker", "rm", "-f", "ecld-registry-accept-m3"]]
    registry.start()
    del docker.commands[:]
    registry.stop()
    assert docker.commands == [
        ["docker", "rm", "-f", "ecld-registry-accept-m3"],
        ["docker", "rmi", f"{HOST}/etny-las", f"{HOST}/etny-securelock", f"{HOST}/etny-trustedzone"],
    ]


def test_stop_without_a_docker_binary_raises_nothing(monkeypatch, tmp_path):
    def missing(args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "docker")
    monkeypatch.setattr(subprocess, "run", missing)
    registry = LocalRegistry("demo", str(tmp_path))
    registry.stop()
    registry.host = HOST
    registry.stop()
