"""The publish's own Kubo: when a publish runs one, how it is registered on
chain, when a previous publish's node is taken over (started again when
stopped) and that it serves from the takeover on, when the swarm port is left
unpublished, when the container is stopped or kept, and that a SIGTERM or
SIGHUP ends the publish as Ctrl+C does. No docker and no network: the Kubo's
API, subprocess.run and the clock are replaced."""
import signal
import subprocess
import types

import pytest

from ethernity_cloud_sdk_py.commands.pynithy import local_kubo
from ethernity_cloud_sdk_py.commands.pynithy.local_kubo import LocalKubo, api_port_in, own_endpoint

PEER = "12D3KooWB8qpxeHqcdb6xTNu3FXjrWE4zTFPverw8XMpms2Qm4pJ"
PORT_ALLOCATED = "docker: Error response from daemon: driver failed programming external connectivity on endpoint " \
                 "ecld-kubo-accept-m3: Bind for 0.0.0.0:4001 failed: port is already allocated"
NO_SUCH_IMAGE = "docker: Error response from daemon: pull access denied for ipfs/kubo"


def _kubo(monkeypatch, addresses):
    monkeypatch.setattr(local_kubo, "free_port", lambda: 5001)
    kubo = LocalKubo("accept m3")
    monkeypatch.setattr(kubo, "_api", lambda command, params=None, timeout=30: {"ID": PEER, "Addresses": addresses})
    return kubo


def test_an_empty_setting_and_the_public_api_mean_a_kubo_of_the_publishs_own():
    assert not own_endpoint("")
    assert not own_endpoint(None)
    assert not own_endpoint("https://ipfs.ethernity.cloud")
    assert not own_endpoint("https://ipfs.ethernity.cloud:443/api/v0")


def test_another_endpoint_is_the_applications_own():
    assert own_endpoint("http://172.18.0.30:5001")
    assert own_endpoint("https://ipfs.example.org/api/v0")


def test_the_container_is_named_after_the_project(monkeypatch):
    assert _kubo(monkeypatch, []).container == "ecld-kubo-accept-m3"


def test_the_first_public_address_is_registered(monkeypatch):
    kubo = _kubo(monkeypatch, [
        f"/ip4/127.0.0.1/tcp/4001/p2p/{PEER}",
        f"/ip4/172.17.0.2/tcp/4001/p2p/{PEER}",
        f"/ip4/80.255.2.15/tcp/4001/p2p/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5/p2p-circuit/p2p/{PEER}",
        f"/ip4/93.255.3.60/tcp/4001/p2p/{PEER}",
        f"/ip4/93.255.3.60/udp/4001/quic-v1/p2p/{PEER}",
    ])
    assert kubo.peer_multiaddr() == f"/ip4/93.255.3.60/tcp/4001/p2p/{PEER}"


def test_behind_nat_the_bare_peer_id_is_registered(monkeypatch):
    kubo = _kubo(monkeypatch, [
        f"/ip4/127.0.0.1/tcp/4001/p2p/{PEER}",
        f"/ip4/192.168.1.20/udp/4001/quic-v1/p2p/{PEER}",
    ])
    assert kubo.peer_multiaddr() == f"/p2p/{PEER}"


def test_the_api_port_of_a_kept_node_is_read_from_docker_port():
    assert api_port_in("127.0.0.1:60823\n") == 60823
    assert api_port_in("127.0.0.1:60823\n[::1]:60823\n") == 60823
    assert api_port_in("") is None
    assert api_port_in("Error: No public port '5001/tcp' published") is None


class _Docker:
    """A subprocess.run that records every command, over a container of the
    project a previous publish left (`kept`, the host port of its API; None
    for none), running unless `stopped`. As docker does, `docker port` fails
    on a stopped container and on none, and `docker start` fails on none and
    starts a stopped one; each `docker run` takes the next entry of
    `refusals` (its stderr, and exit code 125) and exit code 0 once they are
    used up."""

    def __init__(self, kept=None, stopped=False, refusals=()):
        self.commands = []
        self.kept = kept
        self.running = kept is not None and not stopped
        self.refusals = list(refusals)

    def __call__(self, args, **kwargs):
        self.commands.append(list(args))
        if args[:2] == ["docker", "port"]:
            if not self.running:
                stderr = f"Error: No such container: {args[2]}" if self.kept is None else "Error: No public port '5001/tcp' published"
                return subprocess.CompletedProcess(args, 1, stdout="", stderr=stderr)
            return subprocess.CompletedProcess(args, 0, stdout=f"127.0.0.1:{self.kept}\n", stderr="")
        if args[:2] == ["docker", "start"]:
            if self.kept is None:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr=f"Error response from daemon: No such container: {args[2]}")
            self.running = True
            return subprocess.CompletedProcess(args, 0, stdout=f"{args[2]}\n", stderr="")
        if args[:2] == ["docker", "run"] and self.refusals:
            return subprocess.CompletedProcess(args, 125, stdout="", stderr=self.refusals.pop(0))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def runs(self):
        return [c for c in self.commands if c[:2] == ["docker", "run"]]


def _starting(monkeypatch, docker, swarm_port_free=True):
    """A Kubo about to start over `docker`, with the API answering at once
    and no peers to wait for."""
    monkeypatch.setattr(subprocess, "run", docker)
    monkeypatch.setattr(local_kubo, "_port_free", lambda port: swarm_port_free)
    kubo = _kubo(monkeypatch, [])
    kubo.peers = []
    return kubo


def _refusing(command, params=None, timeout=30):
    """The API of a Kubo that does not come up."""
    raise ConnectionError("connection refused")


def _past_the_deadline(monkeypatch):
    """The module's clock advancing 100s on every read, past any deadline the
    Kubo sets, so a wait on its API ends at the first retry without sleeping."""
    clock = {"now": 0.0}

    def now():
        clock["now"] += 100.0
        return clock["now"]
    monkeypatch.setattr(local_kubo, "time", types.SimpleNamespace(time=now, sleep=lambda seconds: None))


def _run_with_swarm_port(kubo):
    return ["docker", "run", "-d", "--name", kubo.container, "-p", "127.0.0.1:5001:5001",
            "-p", "4001:4001", "-p", "4001:4001/udp", local_kubo.KUBO_IMAGE, "daemon", "--init", "--migrate=true"]


def _run_without_swarm_port(kubo):
    return ["docker", "run", "-d", "--name", kubo.container, "-p", "127.0.0.1:5001:5001",
            local_kubo.KUBO_IMAGE, "daemon", "--init", "--migrate=true"]


def test_stop_removes_the_container_with_its_anonymous_volumes_the_ipfs_repo_in_data_ipfs_and_export(monkeypatch):
    docker = _Docker()
    monkeypatch.setattr(subprocess, "run", docker)
    kubo = _kubo(monkeypatch, [])
    kubo.stop()
    assert docker.commands == [["docker", "rm", "-f", "-v", kubo.container]]


@pytest.mark.parametrize("refusals, api", [
    ([NO_SUCH_IMAGE], None),
    ([PORT_ALLOCATED], None),
    ([PORT_ALLOCATED, PORT_ALLOCATED], None),
    ([], _refusing),
])
def test_every_container_the_kubo_removes_is_removed_with_its_anonymous_volumes(monkeypatch, refusals, api):
    """A refused run, a run refused without the swarm port, an API that does
    not come up, and the release."""
    docker = _Docker(refusals=refusals)
    kubo = _starting(monkeypatch, docker)
    if api is not None:
        monkeypatch.setattr(kubo, "_api", api)
        _past_the_deadline(monkeypatch)
    try:
        kubo.start()
    except Exception:
        pass
    kubo.release()
    removals = [c for c in docker.commands if c[:2] == ["docker", "rm"]]
    assert len(removals) >= 2
    assert all(c == ["docker", "rm", "-f", "-v", kubo.container] for c in removals)


def test_stop_without_a_docker_binary_raises_nothing(monkeypatch):
    def missing(args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "docker")
    monkeypatch.setattr(subprocess, "run", missing)
    _kubo(monkeypatch, []).stop()


def test_a_start_publishes_the_free_swarm_port(monkeypatch):
    docker = _Docker()
    kubo = _starting(monkeypatch, docker)
    assert kubo.start() is True
    assert [c[:3] for c in docker.commands] == [
        ["docker", "port", kubo.container], ["docker", "start", kubo.container], ["docker", "rm", "-f"], ["docker", "run", "-d"]]
    assert docker.runs() == [_run_with_swarm_port(kubo)]
    assert kubo.api_url == "http://127.0.0.1:5001"
    assert kubo.serving is False


def test_a_start_leaves_a_swarm_port_in_use_unpublished(monkeypatch):
    docker = _Docker()
    kubo = _starting(monkeypatch, docker, swarm_port_free=False)
    assert kubo.start() is True
    assert docker.runs() == [_run_without_swarm_port(kubo)]


def test_a_swarm_port_another_container_holds_is_left_unpublished(monkeypatch, capsys):
    docker = _Docker(refusals=[PORT_ALLOCATED])
    kubo = _starting(monkeypatch, docker)
    assert kubo.start() is True
    assert [c[:3] for c in docker.commands] == [
        ["docker", "port", kubo.container], ["docker", "start", kubo.container], ["docker", "rm", "-f"], ["docker", "run", "-d"],
        ["docker", "rm", "-f"], ["docker", "run", "-d"]]
    assert docker.runs() == [_run_with_swarm_port(kubo), _run_without_swarm_port(kubo)]
    assert f"\t⚠  swarm port {local_kubo.SWARM_PORT} is allocated to another container; the IPFS node is not dialed directly\n" \
        in capsys.readouterr().out


def test_a_run_docker_refuses_for_another_reason_leaves_no_container(monkeypatch):
    docker = _Docker(refusals=[NO_SUCH_IMAGE])
    kubo = _starting(monkeypatch, docker)
    with pytest.raises(Exception, match="pull access denied"):
        kubo.start()
    assert [c[:3] for c in docker.commands] == [
        ["docker", "port", kubo.container], ["docker", "start", kubo.container], ["docker", "rm", "-f"], ["docker", "run", "-d"],
        ["docker", "rm", "-f"]]
    assert docker.runs() == [_run_with_swarm_port(kubo)]


def test_a_run_refused_without_the_swarm_port_leaves_no_container(monkeypatch):
    docker = _Docker(refusals=[PORT_ALLOCATED, PORT_ALLOCATED])
    kubo = _starting(monkeypatch, docker)
    with pytest.raises(Exception, match="port is already allocated"):
        kubo.start()
    assert [c[:3] for c in docker.commands] == [
        ["docker", "port", kubo.container], ["docker", "start", kubo.container], ["docker", "rm", "-f"], ["docker", "run", "-d"],
        ["docker", "rm", "-f"], ["docker", "run", "-d"], ["docker", "rm", "-f"]]
    assert docker.runs() == [_run_with_swarm_port(kubo), _run_without_swarm_port(kubo)]


def test_a_node_started_afresh_whose_api_does_not_come_up_is_removed(monkeypatch):
    docker = _Docker()
    kubo = _starting(monkeypatch, docker)
    monkeypatch.setattr(kubo, "_api", _refusing)
    _past_the_deadline(monkeypatch)
    with pytest.raises(Exception, match="the Kubo API did not come up in 90s"):
        kubo.start()
    assert [c[:2] for c in docker.commands] == [
        ["docker", "port"], ["docker", "start"], ["docker", "rm"], ["docker", "run"], ["docker", "rm"]]


def test_the_node_of_the_previous_publish_is_taken_over_and_served_from_the_takeover_on(monkeypatch, capsys):
    docker = _Docker(kept=60823)
    kubo = _starting(monkeypatch, docker)
    assert kubo.kept_api_port() == 60823
    assert kubo.start() is True
    assert kubo.api_port == 60823
    assert kubo.api_url == "http://127.0.0.1:60823"
    assert kubo.serving is True
    assert [c[:2] for c in docker.commands] == [["docker", "port"], ["docker", "port"]]
    assert f"IPFS node of the previous publish kept: {kubo.container}" in capsys.readouterr().out


def test_a_stopped_node_of_the_previous_publish_is_started_and_taken_over(monkeypatch, capsys):
    docker = _Docker(kept=60823, stopped=True)
    kubo = _starting(monkeypatch, docker)
    assert kubo.start() is True
    assert docker.commands == [
        ["docker", "port", kubo.container, "5001/tcp"], ["docker", "start", kubo.container], ["docker", "port", kubo.container, "5001/tcp"]]
    assert kubo.api_url == "http://127.0.0.1:60823"
    assert kubo.serving is True
    assert f"IPFS node of the previous publish kept: {kubo.container}" in capsys.readouterr().out


def test_a_taken_over_node_is_kept_on_release(monkeypatch, capsys):
    docker = _Docker(kept=60823)
    kubo = _starting(monkeypatch, docker)
    kubo.start()
    docker.commands.clear()
    kubo.release()
    assert docker.commands == []
    assert "The registered image stays available from this publish's IPFS node" in capsys.readouterr().out


def test_a_taken_over_node_whose_api_does_not_come_up_is_left_to_the_release_which_keeps_it(monkeypatch, capsys):
    docker = _Docker(kept=60823)
    kubo = _starting(monkeypatch, docker)
    monkeypatch.setattr(kubo, "_api", _refusing)
    _past_the_deadline(monkeypatch)
    with pytest.raises(Exception, match="the Kubo API did not come up in 90s"):
        kubo.start()
    assert docker.commands == [["docker", "port", kubo.container, "5001/tcp"]]
    assert kubo.serving is True
    kubo.release()
    assert docker.commands == [["docker", "port", kubo.container, "5001/tcp"]]
    assert "The registered image stays available from this publish's IPFS node" in capsys.readouterr().out


def test_no_node_of_a_previous_publish_means_no_kept_port(monkeypatch):
    docker = _Docker()
    monkeypatch.setattr(subprocess, "run", docker)
    assert _kubo(monkeypatch, []).kept_api_port() is None


def test_a_kubo_serving_no_registered_image_is_stopped_on_release(monkeypatch):
    docker = _Docker()
    monkeypatch.setattr(subprocess, "run", docker)
    kubo = _kubo(monkeypatch, [])
    kubo.release()
    assert docker.commands == [["docker", "rm", "-f", "-v", kubo.container]]


def test_a_kubo_serving_a_registered_image_is_kept_on_release(monkeypatch, capsys):
    docker = _Docker()
    monkeypatch.setattr(subprocess, "run", docker)
    kubo = _kubo(monkeypatch, [])
    kubo.serving = True
    kubo.release()
    out = capsys.readouterr().out
    assert docker.commands == []
    assert "The registered image stays available from this publish's IPFS node, the docker" in out
    assert f"container {kubo.container}, for the extraction service and the bootnode." in out
    assert f"\t   Publish again to retry; `docker rm -f -v {kubo.container}` stops it.\n" in out


def test_a_sigterm_or_sighup_ends_the_publish_as_ctrl_c_does():
    signals = [s for s in (signal.SIGTERM, getattr(signal, "SIGHUP", None)) if s is not None]
    previous = {s: signal.getsignal(s) for s in signals}
    try:
        local_kubo.exit_on_interrupt()
        for s in signals:
            handler = signal.getsignal(s)
            assert handler is not previous[s]
            with pytest.raises(KeyboardInterrupt):
                handler(s, None)
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
