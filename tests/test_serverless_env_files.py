"""ecld-build refuses a .env file in the project's src/serverless.

The build copies src/serverless whole into the securelock's image
(build.copy_backend_to_build_dir), and the image is published: its files are
measured, not encrypted, and the enclaves read no .env file from the image.
build.main() ends with status 1, before it writes, copies or builds anything,
when the directory holds a file named .env or .env.<anything> at any depth,
and its message names each by its path. A file whose name only contains "env"
is not refused, nor is a directory named .env. No docker: main() is stopped at
its next step, validate_esr_config, which the tests replace."""
import os

import pytest

from ethernity_cloud_sdk_py.commands.config import Config
from ethernity_cloud_sdk_py.commands.pynithy import build

HEAD = "ERROR: src/serverless holds a file named .env or .env.<name>:"
TAIL = [
    "       Everything in src/serverless is published with the enclave image and readable by anyone.",
    "       A secret belongs in the task's payload or input, which are encrypted for the enclave.",
    "       Remove each file listed from src/serverless before building.",
]
# Names that are not .env and not .env.<anything>.
OTHER_NAMES = ["env.js", "config.env.json", ".envrc", ".environment", "dotenv", "a.env", "env", ".env_local", "x.env.production"]


class NextStep(Exception):
    """Raised in place of main()'s step after the refusal."""


def _project(tmp_path, names):
    """A dApp project under tmp_path, the working directory: backend.py and
    `names`, paths relative to src/serverless. Returns src/serverless."""
    serverless = tmp_path / "src" / "serverless"
    for name in ["backend.py", *names]:
        path = serverless / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    return serverless


def _tree(root):
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def _main(monkeypatch, tmp_path):
    """build.main() in the project tmp_path, over a .config.json of its own,
    with the step after the refusal raising NextStep."""
    def next_step():
        raise NextStep()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build, "config", Config(tmp_path / ".config.json"))
    monkeypatch.setattr(build, "validate_esr_config", next_step)
    build.main()


def _link(target, link, directory=False):
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"this host makes no symbolic link: {error}")


def test_a_dotenv_file_ends_the_build_with_its_path_and_nothing_copied(monkeypatch, tmp_path, capsys):
    _project(tmp_path, [".env"])
    before = _tree(tmp_path)
    with pytest.raises(SystemExit) as stop:
        _main(monkeypatch, tmp_path)
    assert stop.value.code == 1
    assert capsys.readouterr().out.splitlines() == [HEAD, "       src/serverless/.env", *TAIL]
    assert _tree(tmp_path) == before
    assert not (tmp_path / "build").exists()


def test_a_nested_dotenv_production_ends_the_build_with_its_path_and_nothing_copied(monkeypatch, tmp_path, capsys):
    _project(tmp_path, ["conf/.env.production"])
    before = _tree(tmp_path)
    with pytest.raises(SystemExit) as stop:
        _main(monkeypatch, tmp_path)
    assert stop.value.code == 1
    assert capsys.readouterr().out.splitlines() == [HEAD, "       src/serverless/conf/.env.production", *TAIL]
    assert _tree(tmp_path) == before
    assert not (tmp_path / "build").exists()


def test_every_dotenv_file_is_named_in_the_order_of_its_path(monkeypatch, tmp_path, capsys):
    _project(tmp_path, ["svc/api/.env.local", ".env", "a/.env", ".env.production.bak"])
    with pytest.raises(SystemExit):
        _main(monkeypatch, tmp_path)
    assert capsys.readouterr().out.splitlines() == [
        HEAD,
        "       src/serverless/.env",
        "       src/serverless/.env.production.bak",
        "       src/serverless/a/.env",
        "       src/serverless/svc/api/.env.local",
        *TAIL]


def test_a_file_whose_name_only_holds_env_is_not_refused(monkeypatch, tmp_path, capsys):
    serverless = _project(tmp_path, [*OTHER_NAMES, *(f"lib/{name}" for name in OTHER_NAMES)])
    assert build.serverless_env_files(serverless) == []
    assert build.refuse_serverless_env_files(serverless) is None
    with pytest.raises(NextStep):
        _main(monkeypatch, tmp_path)
    assert "ERROR" not in capsys.readouterr().out


def test_a_directory_named_dotenv_is_entered_and_not_refused(tmp_path):
    serverless = _project(tmp_path, [".env/settings.py", ".env.d/a.py"])
    assert build.serverless_env_files(serverless) == []
    (serverless / ".env.d" / ".env").write_text("x\n", encoding="utf-8")
    assert build.serverless_env_files(serverless) == [".env.d/.env"]


def test_the_project_s_own_dotenv_files_outside_src_serverless_are_not_refused(monkeypatch, tmp_path):
    serverless = _project(tmp_path, [])
    for path in (tmp_path / ".env", tmp_path / "src" / ".env", tmp_path / ".config" / ".env"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("A=1\n", encoding="utf-8")
    assert build.serverless_env_files(serverless) == []
    with pytest.raises(NextStep):
        _main(monkeypatch, tmp_path)


def test_a_project_without_src_serverless_holds_none(tmp_path):
    assert build.serverless_env_files(tmp_path / "src" / "serverless") == []
    assert build.refuse_serverless_env_files(tmp_path / "src" / "serverless") is None


def test_a_link_named_dotenv_is_refused_and_a_linked_directory_is_entered_once(tmp_path):
    serverless = _project(tmp_path, ["conf/settings.py"])
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".env.staging").write_text("A=1\n", encoding="utf-8")
    (tmp_path / "secrets.txt").write_text("A=1\n", encoding="utf-8")
    _link(tmp_path / "secrets.txt", serverless / ".env")
    _link(outside, serverless / "shared", directory=True)
    _link(serverless, serverless / "conf" / "again", directory=True)
    _link(tmp_path / "none", serverless / "conf" / ".env.gone")
    assert build.serverless_env_files(serverless) == [".env", "conf/.env.gone", "shared/.env.staging"]


@pytest.mark.parametrize("name, refused", [
    (".env", True), (".env.", True), (".env.production", True), (".env.a.b", True), (".env.a\nb", True),
    *((name, False) for name in OTHER_NAMES), (".ENV", False), (" .env", False), (".env ", False), ("", False),
])
def test_the_name_rule(name, refused):
    assert bool(build.SERVERLESS_ENV_FILE.fullmatch(name)) is refused
