"""The securelock's binary-fs is made of the enclave's root, whole:
binary-fs-build.sh runs scone binary-fs over /enclave-root with no pattern
that selects a part of it, with its symbolic links kept as they are and with
the host's two name-resolution files passed through. No build script runs
PyInstaller or reads a list of collected objects."""
from pathlib import Path

SCRIPTS = (Path(__file__).resolve().parent.parent / "ethernity_cloud_sdk_py" / "commands" / "pynithy" / "build"
           / "securelock" / "scripts")
BUILD_SCRIPTS = ["binary-fs-build.sh", "enclave-root.sh", "pyfreeze.sh"]


def _commands(name):
    """The lines of a build script that are not comments, stripped."""
    lines = (SCRIPTS / name).read_text(encoding="utf-8").split("\n")
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def test_the_binary_fs_is_made_of_the_enclave_s_root_and_of_nothing_else():
    assert _commands("binary-fs-build.sh") == [
        "set -e",
        "EXEC=(scone binary-fs /enclave-root /binary-fs-dir -v \\",
        "--preserve-symbolic-links \\",
        "--host-path=/etc/resolv.conf \\",
        "--host-path=/etc/hosts)",
        'echo "${EXEC[@]}"',
        "mkdir -p /binary-fs-dir",
        'SCONE_MODE=auto exec "${EXEC[@]}"',
    ]


def test_no_build_script_runs_pyinstaller_or_reads_a_list_of_collected_objects():
    assert sorted(p.name for p in SCRIPTS.glob("*.sh") if p.name != "get_keygen_so.sh") == BUILD_SCRIPTS
    for name in BUILD_SCRIPTS:
        text = "\n".join(_commands(name))
        assert "pyinstaller" not in text.lower(), name
        assert "COLLECT" not in text and "--include" not in text, name
