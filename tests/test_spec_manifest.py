"""tests/spec/ holds byte-identical copies of pox-smart-contract-litvm's
spec/*.json and spec/metadata-keys.md at SOURCE_COMMIT, and MANIFEST.sha256,
the lines of that commit's spec/MANIFEST.sha256 for the copies held (the
sha256sum text format, names sorted bytewise, LF lines; `sha256sum -c
MANIFEST.sha256` in tests/spec checks every copy). Every copy must hash to
its manifest line and the manifest must name every copy and nothing else.
Where a pox-smart-contract-litvm checkout is found (POX_SMART_CONTRACT_LITVM,
else a directory of that name beside this repository or beside any directory
above it), every copy must equal `git show SOURCE_COMMIT:spec/<name>` and
every manifest line that commit's line, never the checkout's working tree."""
import hashlib
import re
from pathlib import Path

import pytest

from tests.litvm_checkout import LITVM_ENV, SOURCE_COMMIT, litvm_checkout, shown_at

SPEC = Path(__file__).resolve().parent / "spec"
MANIFEST = "MANIFEST.sha256"
LINE = re.compile(r"^([0-9a-f]{64})  ([^\s/]+)$")


def parse(text):
    entries = []
    for line in text.rstrip("\n").split("\n"):
        m = LINE.match(line)
        assert m, "manifest line is not '<sha256>  <name>': %r" % line
        entries.append((m.group(2), m.group(1)))
    return entries


def manifest():
    text = (SPEC / MANIFEST).read_bytes()
    assert text.endswith(b"\n") and b"\r" not in text
    return parse(text.decode("ascii"))


def shown_at_source(checkout, name):
    shown = shown_at(checkout, f"spec/{name}")
    assert shown is not None, f"{checkout} holds no {SOURCE_COMMIT}:spec/{name}"
    return shown


def test_the_manifest_names_every_copy_once_in_bytewise_order():
    names = [name for name, _ in manifest()]
    copies = sorted(p.name for p in SPEC.iterdir() if p.name != MANIFEST)
    assert names == sorted(names, key=lambda n: n.encode())
    assert len(set(names)) == len(names)
    assert names == copies


@pytest.mark.parametrize("name,digest", manifest(), ids=[n for n, _ in manifest()])
def test_each_copy_hashes_to_its_manifest_line(name, digest):
    assert hashlib.sha256((SPEC / name).read_bytes()).hexdigest() == digest


def test_the_copies_and_the_manifest_lines_are_the_source_commit_s():
    checkout = litvm_checkout()
    if checkout is None:
        pytest.skip(f"no pox-smart-contract-litvm checkout found (set {LITVM_ENV})")
    lines = dict(parse(shown_at_source(checkout, MANIFEST).decode("ascii")))
    for name, digest in manifest():
        assert digest == lines[name], name
        assert (SPEC / name).read_bytes() == shown_at_source(checkout, name), name
