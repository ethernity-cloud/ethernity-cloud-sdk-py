"""The publish's own Kubo: when a publish runs one, how it is registered on
chain, and when it is stopped. No docker and no network: the Kubo's API and
`docker rm` are replaced."""
import subprocess

from ethernity_cloud_sdk_py.commands.pynithy import local_kubo
from ethernity_cloud_sdk_py.commands.pynithy.local_kubo import LocalKubo, api_port_in, own_endpoint

PEER = "12D3KooWB8qpxeHqcdb6xTNu3FXjrWE4zTFPverw8XMpms2Qm4pJ"


def _kubo(monkeypatch, addresses):
    monkeypatch.setattr(local_kubo, "_free_port", lambda: 5001)
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


def _removals(monkeypatch):
    removed = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: removed.append(args))
    return removed


def test_a_kubo_serving_no_registered_image_is_stopped_on_release(monkeypatch):
    removed = _removals(monkeypatch)
    kubo = _kubo(monkeypatch, [])
    kubo.release()
    assert removed == [["docker", "rm", "-f", kubo.container]]


def test_a_kubo_serving_a_registered_image_is_kept_on_release(monkeypatch, capsys):
    removed = _removals(monkeypatch)
    kubo = _kubo(monkeypatch, [])
    kubo.serving = True
    kubo.release()
    assert removed == []
    assert kubo.container in capsys.readouterr().out
