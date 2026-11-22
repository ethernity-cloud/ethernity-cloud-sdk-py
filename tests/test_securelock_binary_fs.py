"""The securelock's binary-fs holds no output of PyInstaller: binary-fs-build.sh
reads the shared objects PyInstaller collected from its toc, removes build/,
dist/, the .spec file and the toc, and then runs scone binary-fs with the
collected objects among its includes."""
import re
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent / "ethernity_cloud_sdk_py" / "commands" / "pynithy" / "build"
          / "securelock" / "scripts" / "binary-fs-build.sh")


def _lines():
    return [line.strip() for line in SCRIPT.read_text(encoding="utf-8").split("\n")]


def test_the_toc_is_read_then_pyinstallers_output_removed_then_the_binary_fs_made():
    lines = _lines()
    read = next(i for i, line in enumerate(lines) if "COLLECTED_SO+=" in line and "COLLECT-00.toc" in line)
    remove = lines.index("rm -rf ./build ./dist ./*.spec ./COLLECT-00.toc")
    make = next(i for i, line in enumerate(lines) if line.startswith("EXEC=(scone binary-fs "))
    run = lines.index('SCONE_MODE=auto exec "${EXEC[@]}"')
    assert read < remove < make < run


def test_the_collected_objects_are_included_from_the_list_read_before_the_removal():
    text = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r'^for FILE in "\$\{COLLECTED_SO\[@\]\}"; do\n  EXEC\+=\(--include "\$\{FILE\}"\'\*\'\)\ndone$', text, re.M)
    assert "$(grep '.so' ./COLLECT-00.toc" not in text, "the toc is read once, before it is removed"
