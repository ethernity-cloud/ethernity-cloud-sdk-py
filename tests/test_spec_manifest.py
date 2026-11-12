"""tests/spec/ holds byte-identical copies of pox-smart-contract-litvm's
spec/*.json and spec/metadata-keys.md with that directory's
spec/MANIFEST.sha256 (the sha256sum text format, names sorted bytewise, LF
lines). Every copy must hash to its manifest line, the manifest must name
every copy and nothing else, and, where a pox-smart-contract-litvm checkout
sits beside this repository, every copy and the manifest itself must equal
the source bytes."""
import hashlib
import re
from pathlib import Path

import pytest

SPEC = Path(__file__).resolve().parent / "spec"
REPO = Path(__file__).resolve().parents[1]
MANIFEST = "MANIFEST.sha256"
LINE = re.compile(r"^([0-9a-f]{64})  ([^\s/]+)$")


def manifest_lines():
    text = (SPEC / MANIFEST).read_bytes()
    assert text.endswith(b"\n") and b"\r" not in text
    return text.decode("ascii").rstrip("\n").split("\n")


def manifest():
    entries = []
    for line in manifest_lines():
        m = LINE.match(line)
        assert m, "manifest line is not '<sha256>  <name>': %r" % line
        entries.append((m.group(2), m.group(1)))
    return entries


def source_dir():
    for base in (REPO.parent, REPO.parents[1]):
        candidate = base / "pox-smart-contract-litvm" / "spec"
        if (candidate / MANIFEST).is_file():
            return candidate
    return None


def test_the_manifest_names_every_copy_once_in_bytewise_order():
    names = [name for name, _ in manifest()]
    copies = sorted(p.name for p in SPEC.iterdir() if p.name != MANIFEST)
    assert names == sorted(names, key=lambda n: n.encode())
    assert len(set(names)) == len(names)
    assert names == copies


@pytest.mark.parametrize("name,digest", manifest(), ids=[n for n, _ in manifest()])
def test_each_copy_hashes_to_its_manifest_line(name, digest):
    assert hashlib.sha256((SPEC / name).read_bytes()).hexdigest() == digest


def test_the_copies_equal_the_litvm_checkout_when_present():
    source = source_dir()
    if source is None:
        pytest.skip("no pox-smart-contract-litvm checkout beside this repository")
    for name in [MANIFEST] + [name for name, _ in manifest()]:
        assert (SPEC / name).read_bytes() == (source / name).read_bytes(), name
