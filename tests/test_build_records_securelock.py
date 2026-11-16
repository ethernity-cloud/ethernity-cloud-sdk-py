"""ecld-build records the securelock it pushed in .config.json, for the
publish to check its tree against: SECURELOCK_IMAGE_DIGEST, the digest of
the pushed manifest, and MRENCLAVE_SECURELOCK_SIGNED, the image's
/signed_mrenclave.txt. No docker: the command runner is replaced."""
import pytest

from ethernity_cloud_sdk_py.commands.config import Config
from ethernity_cloud_sdk_py.commands.pynithy import build

DIGEST = "sha256:" + "ab" * 32
SIGNED = "81a7f8e8" + "0" * 56


def _build(monkeypatch, tmp_path, outputs):
    """build.config over a .config.json of its own, and get_command_output
    answering each command with the output of the first key it contains."""
    cfg = Config(tmp_path / ".config.json")
    monkeypatch.setattr(build, "config", cfg)
    commands = []

    def run(command):
        commands.append(command)
        return next((out for key, out in outputs.items() if key in command), None)

    monkeypatch.setattr(build, "get_command_output", run)
    return cfg, commands


def test_the_build_records_the_pushed_digest_and_the_signed_mrenclave(monkeypatch, tmp_path):
    cfg, commands = _build(monkeypatch, tmp_path, {
        "docker inspect": f"localhost:5000/etny-securelock@{DIGEST}\n",
        "signed_mrenclave": f"{SIGNED}\n"})
    assert build.record_securelock_image() is True
    assert cfg.read("SECURELOCK_IMAGE_DIGEST") == DIGEST
    assert cfg.read("MRENCLAVE_SECURELOCK_SIGNED") == SIGNED
    assert commands == [
        'docker inspect localhost:5000/etny-securelock --format "{{range .RepoDigests}}{{println .}}{{end}}"',
        "docker run --rm --entrypoint cat etny-securelock:latest /signed_mrenclave.txt"]
    assert Config(tmp_path / ".config.json").load()["SECURELOCK_IMAGE_DIGEST"] == DIGEST


def test_the_digest_is_the_local_registrys_among_several(monkeypatch, tmp_path):
    cfg, _ = _build(monkeypatch, tmp_path, {
        "docker inspect": f"registry.example/team/etny-securelock@sha256:{'ff' * 32}\nlocalhost:5000/etny-securelock@{DIGEST}\n",
        "signed_mrenclave": SIGNED})
    build.record_securelock_image()
    assert cfg.read("SECURELOCK_IMAGE_DIGEST") == DIGEST


def test_a_push_docker_records_no_digest_of_fails_the_build(monkeypatch, tmp_path, capsys):
    cfg, _ = _build(monkeypatch, tmp_path, {"docker inspect": "", "signed_mrenclave": SIGNED})
    with pytest.raises(SystemExit) as exit_:
        build.record_securelock_image()
    assert exit_.value.code == 1
    assert "ERROR: docker records no digest of localhost:5000/etny-securelock after the push" in capsys.readouterr().out
    assert cfg.read("SECURELOCK_IMAGE_DIGEST") is None
    assert cfg.read("MRENCLAVE_SECURELOCK_SIGNED") is None


def test_an_image_without_a_signed_mrenclave_fails_the_build(monkeypatch, tmp_path, capsys):
    cfg, _ = _build(monkeypatch, tmp_path, {
        "docker inspect": f"localhost:5000/etny-securelock@{DIGEST}\n",
        "signed_mrenclave": "cat: can't open '/signed_mrenclave.txt': No such file or directory"})
    with pytest.raises(SystemExit) as exit_:
        build.record_securelock_image()
    assert exit_.value.code == 1
    assert "ERROR: the securelock image holds no signed MRENCLAVE in /signed_mrenclave.txt" in capsys.readouterr().out
    assert cfg.read("SECURELOCK_IMAGE_DIGEST") is None
