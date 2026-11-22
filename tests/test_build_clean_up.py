"""ecld-build's removal of the containers a previous build or publish left:
`docker ps` runs from an argument list, without a shell, with the exact-name
filter as one argument, and every id it lists is stopped and removed with its
anonymous volumes. No docker: subprocess.run and the command runner are
replaced."""
import subprocess
import sys

from ethernity_cloud_sdk_py.commands.pynithy import build

NAMES = ("registry", "las", "etny-securelock", "etny-trustedzone", "etny-swift-stream")


def _clean_up(monkeypatch, tmp_path, listed, returncode=0):
    """clean_up_registry run in `tmp_path`, with `docker ps` exiting
    `returncode` and listing for each name the ids `listed` gives it; the
    `docker ps` calls (arguments and keywords) and the commands run_command
    was given."""
    monkeypatch.chdir(tmp_path)
    ps = []
    commands = []

    def run(args, **kwargs):
        ps.append((list(args), kwargs))
        name = args[-1][len("name=^/"):-1]
        out = "".join(f"{i}\n" for i in listed.get(name, ()))
        return subprocess.CompletedProcess(args, returncode, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(build, "run_command", lambda command, redirect_output=False: commands.append(command))
    monkeypatch.setattr(build, "get_command_output", lambda command: "")
    assert build.clean_up_registry() is True
    return ps, commands


def test_docker_ps_runs_without_a_shell_with_the_exact_name_filter_as_one_argument(monkeypatch, tmp_path):
    ps, commands = _clean_up(monkeypatch, tmp_path, {})
    assert [args for args, _ in ps] == [["docker", "ps", "-a", "-q", "--filter", f"name=^/{name}$"] for name in NAMES]
    assert all(not kwargs.get("shell") for _, kwargs in ps)
    assert commands == []


def test_every_id_docker_ps_lists_is_stopped_and_removed_with_its_anonymous_volumes(monkeypatch, tmp_path):
    _, commands = _clean_up(monkeypatch, tmp_path, {
        "registry": ["0a1b2c3d4e5f"],
        "las": ["111111111111", "222222222222"]})
    assert commands == [
        "docker stop 0a1b2c3d4e5f",
        "docker rm -f -v 0a1b2c3d4e5f",
        "docker stop 111111111111 222222222222",
        "docker rm -f -v 111111111111 222222222222"]


def test_a_docker_ps_that_fails_removes_nothing(monkeypatch, tmp_path):
    _, commands = _clean_up(monkeypatch, tmp_path, {"registry": ["0a1b2c3d4e5f"]}, returncode=1)
    assert commands == []


def test_the_filter_reaches_the_process_unchanged(monkeypatch):
    """container_ids with docker replaced by a Python process that prints its
    last argument: the filter arrives as `name=^/registry$`, its caret and
    dollar sign kept and no quote added, on this OS's process creation."""
    real_run = subprocess.run

    def echo(args, **kwargs):
        assert args[0] == "docker"
        return real_run([sys.executable, "-c", "import sys; print(sys.argv[-1])"] + list(args[1:]), **kwargs)

    monkeypatch.setattr(subprocess, "run", echo)
    assert build.container_ids("registry") == ["name=^/registry$"]
