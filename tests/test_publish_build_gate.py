"""ecld-publish measures the securelock from the project's own image tree,
served by a registry of the project's own, and refuses an image other than
the one ecld-build recorded; when it ends the registry is removed and the
Kubo released, which keeps it while it serves a registered image whose
certificate is not on chain, and a SIGTERM or SIGHUP ends it as Ctrl+C does
(`exit_on_interrupt`). No docker, no chain and no SGX: the docker steps and
the containers are replaced. publish.py builds an ImageRegistry
from the .config.json of the CWD at import, so the module is imported from
a directory holding one."""
import importlib
import json

import pytest
import yaml

from ethernity_cloud_sdk_py.commands.config import Config
from ethernity_cloud_sdk_py.commands.spinner import Spinner

DIGEST = "sha256:" + "ab" * 32
OTHER_DIGEST = "sha256:" + "cd" * 32
SIGNED = "81a7f8e8" + "0" * 56
OTHER_MRENCLAVE = "571eb7c6" + "0" * 56
HOST = "127.0.0.1:49153"
KEY = "0x" + "11" * 32
PEER = "12D3KooWB8qpxeHqcdb6xTNu3FXjrWE4zTFPverw8XMpms2Qm4pJ"
IMAGE_CID = "QmImage" + "a" * 39
COMPOSE_CID = "QmCompose" + "b" * 37


def _publish_module(monkeypatch, tmp_path, **changes):
    """The publish module with its config over a .config.json in tmp_path,
    the CWD: a build for IOTEX_TESTNET, a self-signing network with a LAS,
    recorded as DIGEST and SIGNED. A key set to None is left out."""
    stored = {
        "PROJECT_NAME": "demo", "BLOCKCHAIN_NETWORK": "IOTEX_TESTNET", "DAPP_TYPE": "Pynithy", "VERSION": 1,
        "SECURELOCK_SESSION": "demo_SECURELOCK_V3_testnet_1", "MEMORY_TO_ALLOCATE": "1G",
        "SECURELOCK_IMAGE_DIGEST": DIGEST, "MRENCLAVE_SECURELOCK_SIGNED": SIGNED, "MRENCLAVE_SECURELOCK": SIGNED,
    }
    stored.update(changes)
    stored = {key: value for key, value in stored.items() if value is not None}
    (tmp_path / ".config.json").write_text(json.dumps(stored), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ECLD_IPFS_ENDPOINT", raising=False)
    monkeypatch.delenv("ECLD_IPFS_TOKEN", raising=False)
    publish = importlib.import_module("ethernity_cloud_sdk_py.commands.pynithy.publish")
    cfg = Config(tmp_path / ".config.json")
    cfg.load()
    monkeypatch.setattr(publish, "config", cfg)
    return publish


class _Registry:
    def __init__(self, name, tree):
        self.container = f"ecld-registry-{name}"
        self.tree = tree
        self.host = None
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1
        self.host = HOST
        return True

    def image(self, repository):
        return f"{self.host}/{repository}"

    def stop(self):
        self.stopped += 1


class _Kubo:
    """The publish's Kubo, whose start ends in `start_outcome`: an exception
    raised, or None for a start that completes. `released` counts the
    release calls; what a release does with the container is LocalKubo's
    (test_local_kubo.py)."""

    def __init__(self, name, start_outcome=None):
        self.container = f"ecld-kubo-{name}"
        self.api_url = "http://127.0.0.1:5001"
        self.start_outcome = start_outcome
        self.serving = False
        self.started = 0
        self.stopped = 0
        self.released = 0
        self.provided = []

    def start(self):
        self.started += 1
        if self.start_outcome is not None:
            raise self.start_outcome
        return True

    def peer_multiaddr(self):
        return f"/p2p/{PEER}"

    def provide(self, cid):
        self.provided.append(cid)

    def stop(self):
        self.stopped += 1

    def release(self):
        self.released += 1


class _NoChain:
    """An image registry whose first read ends the run."""

    def set_private_key(self, private_key):
        pass

    def is_v2(self):
        raise Exception("no chain in this test")


class _TwoStepRegistry:
    """A registry that records an image before its certificate. The
    registration raises `register_outcome` and the certificate's write
    `cert_outcome` when they are exceptions."""

    def __init__(self, register_outcome=None, cert_outcome=None):
        self.register_outcome = register_outcome
        self.cert_outcome = cert_outcome
        self.registered = []
        self.certified = []

    def set_private_key(self, private_key):
        pass

    def is_v2(self):
        return True

    def register_image(self, ipfs_hash, docker_compose_hash, ipfs_peer):
        if self.register_outcome is not None:
            raise self.register_outcome
        self.registered.append((ipfs_hash, docker_compose_hash, ipfs_peer))

    def set_image_cert(self, ipfs_hash, certificate):
        if self.cert_outcome is not None:
            raise self.cert_outcome
        self.certified.append((ipfs_hash, certificate))


class _IPFS:
    """An IPFS client whose add of the compose file gives COMPOSE_CID and of
    anything else IMAGE_CID."""

    api_url = "http://127.0.0.1:5001"
    headers = {}

    def upload(self, path, only_hash=False):
        return COMPOSE_CID if path == "docker-compose-final.yml" else IMAGE_CID


def test_the_local_compose_names_the_builds_registry_and_the_shipped_one_localhost(monkeypatch, tmp_path):
    publish = _publish_module(monkeypatch, tmp_path)
    dest = tmp_path / "build" / "securelock" / "run"
    assert publish.update_docker_compose_files(dest, HOST) is True
    local = yaml.safe_load((dest / "docker-compose.yml").read_text(encoding="utf-8"))
    final = yaml.safe_load((dest / "docker-compose-final.yml").read_text(encoding="utf-8"))
    assert {name: service["image"] for name, service in local["services"].items()} == {
        "las": f"{HOST}/etny-las",
        "etny-securelock": f"{HOST}/etny-securelock",
        "etny-trustedzone": f"{HOST}/etny-trustedzone"}
    assert {name: service["image"] for name, service in final["services"].items()} == {
        "las": "localhost:5000/etny-las",
        "etny-securelock": "localhost:5000/etny-securelock",
        "etny-trustedzone": "localhost:5000/etny-trustedzone"}
    assert "localhost:5000" not in (dest / "docker-compose.yml").read_text(encoding="utf-8")


def _docker(monkeypatch, publish, measured, served):
    """The docker steps of _publish up to the gate, with the image whose
    digest was asked for recorded."""
    inspected = []
    monkeypatch.setattr(publish, "get_docker_server_info", lambda: True)
    monkeypatch.setattr(publish, "update_docker_compose_files", lambda dest, host: True)
    monkeypatch.setattr(publish, "pull_build_images", lambda: True)
    monkeypatch.setattr(publish, "extract_scone_hash", lambda service: measured)
    monkeypatch.setattr(publish, "served_securelock_digest", lambda image: inspected.append(image) or served)
    monkeypatch.setattr(publish, "image_registry", _NoChain())
    return inspected


def _run_publish(publish, tmp_path):
    registry = _Registry("demo", str(tmp_path / "build" / "registry"))
    with pytest.raises(SystemExit) as exit_:
        publish._publish(KEY, Spinner(), None, None, registry, (DIGEST, SIGNED))
    return exit_.value.code, registry


def test_the_gate_refuses_a_tree_serving_another_digest(monkeypatch, tmp_path, capsys):
    publish = _publish_module(monkeypatch, tmp_path)
    inspected = _docker(monkeypatch, publish, SIGNED, OTHER_DIGEST)
    code, registry = _run_publish(publish, tmp_path)
    out = capsys.readouterr().out
    assert code == 1
    assert registry.started == 1
    assert inspected == [f"{HOST}/etny-securelock"]
    assert (f"Error: the securelock served by this build's image tree has digest {OTHER_DIGEST}, "
            f"not the SECURELOCK_IMAGE_DIGEST recorded at build ({DIGEST}).") in out
    assert "The tree does not hold the image ecld-build recorded. Refusing to publish; run ecld-build again." in out
    assert "The measured securelock is this build's image" not in out


def test_the_gate_refuses_a_measurement_other_than_the_signed_one_recorded(monkeypatch, tmp_path, capsys):
    publish = _publish_module(monkeypatch, tmp_path)
    _docker(monkeypatch, publish, OTHER_MRENCLAVE, DIGEST)
    code, _ = _run_publish(publish, tmp_path)
    out = capsys.readouterr().out
    assert code == 1
    assert (f"Error: securelock runtime MRENCLAVE ({OTHER_MRENCLAVE}) != MRENCLAVE_SECURELOCK_SIGNED "
            f"recorded at build ({SIGNED}).") in out
    assert "The measured enclave is not the one ecld-build signed. Refusing to publish; run ecld-build again." in out
    assert "The measured securelock is this build's image" not in out


def test_the_gate_passes_the_builds_own_image(monkeypatch, tmp_path, capsys):
    publish = _publish_module(monkeypatch, tmp_path)
    _docker(monkeypatch, publish, SIGNED, DIGEST)
    code, _ = _run_publish(publish, tmp_path)
    out = capsys.readouterr().out
    assert code == 1
    assert f"The measured securelock is this build's image ({DIGEST})" in out
    assert "Could not read the image registry: no chain in this test" in out
    assert "Refusing to publish" not in out


def test_a_measurement_that_fails_ends_the_publish(monkeypatch, tmp_path, capsys):
    publish = _publish_module(monkeypatch, tmp_path)
    inspected = _docker(monkeypatch, publish, None, DIGEST)
    code, _ = _run_publish(publish, tmp_path)
    assert code == 1
    assert inspected == []
    assert "Error:" not in capsys.readouterr().out


def _two_step_publish(monkeypatch, publish, tmp_path, registry_):
    """_publish on `registry_` past the gate, with the image uploaded by
    _IPFS and the certificate extracted locally and taken as valid. Returns
    the exit code (None when the publish completes) and the Kubo."""
    _docker(monkeypatch, publish, SIGNED, DIGEST)
    monkeypatch.setattr(publish, "image_registry", registry_)
    monkeypatch.setattr(publish, "extract_public_key_local",
                        lambda: "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----")
    monkeypatch.setattr(publish, "_is_valid_certificate", lambda value: True)
    kubo = _Kubo("demo")
    local_registry = _Registry("demo", str(tmp_path / "build" / "registry"))
    try:
        publish._publish(KEY, Spinner(), _IPFS(), kubo, local_registry, (DIGEST, SIGNED))
    except SystemExit as exit_:
        return exit_.code, kubo
    return None, kubo


def test_the_kubo_serves_the_registered_image_until_its_certificate_is_on_chain(monkeypatch, tmp_path):
    publish = _publish_module(monkeypatch, tmp_path)
    registry_ = _TwoStepRegistry()
    code, kubo = _two_step_publish(monkeypatch, publish, tmp_path, registry_)
    assert code is None
    assert registry_.registered == [(IMAGE_CID, COMPOSE_CID, f"/p2p/{PEER}")]
    assert [cid for cid, _ in registry_.certified] == [IMAGE_CID]
    assert kubo.provided == [IMAGE_CID, COMPOSE_CID]
    assert kubo.serving is False


def test_a_publish_that_ends_before_the_certificate_leaves_the_kubo_serving(monkeypatch, tmp_path, capsys):
    publish = _publish_module(monkeypatch, tmp_path)
    registry_ = _TwoStepRegistry(cert_outcome=Exception("the chain refused the certificate"))
    code, kubo = _two_step_publish(monkeypatch, publish, tmp_path, registry_)
    assert code == 1
    assert len(registry_.registered) == 1 and registry_.certified == []
    assert "the chain refused the certificate" in capsys.readouterr().out
    assert kubo.serving is True


def test_a_registration_that_fails_leaves_the_kubo_serving_nothing(monkeypatch, tmp_path, capsys):
    publish = _publish_module(monkeypatch, tmp_path)
    registry_ = _TwoStepRegistry(register_outcome=Exception("demo is owned by another wallet"))
    code, kubo = _two_step_publish(monkeypatch, publish, tmp_path, registry_)
    assert code == 1
    assert registry_.registered == [] and registry_.certified == []
    assert "demo is owned by another wallet" in capsys.readouterr().out
    assert kubo.serving is False


def _main(monkeypatch, publish, outcome, kubo_start=None):
    """publish.main with the containers replaced, the Kubo's start ending in
    `kubo_start` and _publish in `outcome`: an exception raised, or None for
    one that completes. Returns the containers by name and the list of
    _publish calls."""
    containers = {}
    published = []
    # The signal handlers are the process's; `exit_on_interrupt` is checked on
    # its own in test_local_kubo.py.
    monkeypatch.setattr(publish, "exit_on_interrupt", lambda: None)
    monkeypatch.setattr(publish, "LocalKubo", lambda name: containers.setdefault("kubo", _Kubo(name, kubo_start)))
    monkeypatch.setattr(publish, "LocalRegistry",
                        lambda name, tree: containers.setdefault("registry", _Registry(name, tree)))
    monkeypatch.setattr(publish, "image_registry", _NoChain())

    def _publish(*args, **kwargs):
        published.append(args)
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(publish, "_publish", _publish)
    return containers, published


def _ended(containers):
    """(registry removals, Kubo releases, Kubo removals) when main ended."""
    return containers["registry"].stopped, containers["kubo"].released, containers["kubo"].stopped


def test_the_registry_is_removed_and_the_kubo_released_when_the_publish_fails(monkeypatch, tmp_path):
    publish = _publish_module(monkeypatch, tmp_path)
    containers, published = _main(monkeypatch, publish, SystemExit(1))
    with pytest.raises(SystemExit):
        publish.main(KEY)
    assert len(published) == 1
    assert containers["kubo"].container == "ecld-kubo-demo"
    assert containers["registry"].container == "ecld-registry-demo"
    assert containers["registry"].tree == str(tmp_path / "build" / "registry")
    assert containers["kubo"].started == 1
    assert _ended(containers) == (1, 1, 0)


def test_main_makes_a_sigterm_or_sighup_end_the_publish_as_ctrl_c_does(monkeypatch, tmp_path):
    publish = _publish_module(monkeypatch, tmp_path)
    _main(monkeypatch, publish, None)
    installed = []
    monkeypatch.setattr(publish, "exit_on_interrupt", lambda: installed.append(True))
    publish.main(KEY)
    assert installed == [True]


def test_an_interrupted_publish_removes_the_registry_and_releases_the_kubo_too(monkeypatch, tmp_path):
    publish = _publish_module(monkeypatch, tmp_path)
    containers, _ = _main(monkeypatch, publish, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        publish.main(KEY)
    assert _ended(containers) == (1, 1, 0)


def test_a_publish_that_completes_removes_the_registry_and_releases_the_kubo(monkeypatch, tmp_path):
    publish = _publish_module(monkeypatch, tmp_path)
    containers, published = _main(monkeypatch, publish, None)
    publish.main(KEY)
    assert len(published) == 1
    assert containers["kubo"].started == 1
    assert _ended(containers) == (1, 1, 0)


def test_a_kubo_that_does_not_start_ends_the_publish_with_the_registry_removed(monkeypatch, tmp_path, capsys):
    publish = _publish_module(monkeypatch, tmp_path)
    refused = "docker could not start ipfs/kubo:release: Bind for 0.0.0.0:4001 failed: port is already allocated"
    containers, published = _main(monkeypatch, publish, None, kubo_start=Exception(refused))
    with pytest.raises(SystemExit) as exit_:
        publish.main(KEY)
    assert exit_.value.code == 1
    assert published == []
    assert refused in capsys.readouterr().out
    assert containers["kubo"].started == 1
    assert _ended(containers) == (1, 1, 0)


def test_an_interrupt_while_the_kubo_starts_removes_the_registry_and_releases_the_kubo(monkeypatch, tmp_path):
    publish = _publish_module(monkeypatch, tmp_path)
    containers, published = _main(monkeypatch, publish, None, kubo_start=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        publish.main(KEY)
    assert published == []
    assert _ended(containers) == (1, 1, 0)


@pytest.mark.parametrize("missing", [
    {"SECURELOCK_IMAGE_DIGEST": None},
    {"MRENCLAVE_SECURELOCK_SIGNED": None},
    {"SECURELOCK_IMAGE_DIGEST": None, "MRENCLAVE_SECURELOCK_SIGNED": None},
    {"SECURELOCK_IMAGE_DIGEST": ""},
])
def test_a_build_by_an_older_sdk_ends_the_publish_before_anything_starts(monkeypatch, tmp_path, capsys, missing):
    publish = _publish_module(monkeypatch, tmp_path, **missing)
    containers, published = _main(monkeypatch, publish, None)
    with pytest.raises(SystemExit) as exit_:
        publish.main(KEY)
    out = capsys.readouterr().out
    assert exit_.value.code == 1
    assert "Error: this build records no SECURELOCK_IMAGE_DIGEST and MRENCLAVE_SECURELOCK_SIGNED." in out
    assert ("The securelock was built by an SDK older than these records; "
            "run ecld-build with the current SDK, then publish again.") in out
    assert containers == {} and published == []
