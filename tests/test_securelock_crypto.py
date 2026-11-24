"""The securelock's crypto copy against the trustedzone's (MATCHING-SYNC-PLAN.md
4.7 SDK-09): every function commands/pynithy/build/securelock/src/etny_crypto.py
shares by name with etny-pynithy's v3/build/trustedzone/src/etny_crypto.py is
the same text as `git show COMMIT:<path>` of the trustedzone (each line
compared without its trailing white space), comment lines that hold a print
call left out on both sides: the securelock carries none
(tests/test_f_log_confidentiality.py refuses them); the copy holds no function of its
own but `encrypt`, no function of it carries the trustedzone's statement form
(`ecld-stmt-1`, spec reserved-prefixes.json), and its header states that the
statement signer is the trustedzone's alone. The checkout is ETNY_PYNITHY,
else an etny-pynithy beside this repository or beside a directory above it.
ethernity-cloud-sdk-js's pynithy copy is the same file (its
test/pynithyMirror.test.js) and test/securelockCrypto.test.js holds the same
comparison."""
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COPY = REPO / "ethernity_cloud_sdk_py" / "commands" / "pynithy" / "build" / "securelock" / "src" / "etny_crypto.py"
ENV = "ETNY_PYNITHY"
# The etny-pynithy commit the copy is compared with (feature/matching).
COMMIT = "fb8f8b4"
SOURCE = "v3/build/trustedzone/src/etny_crypto.py"
OWN = ["encrypt"]


def checkout():
    candidates = [Path(os.environ[ENV]).resolve()] if os.environ.get(ENV) else []
    candidates += [base.parent / "etny-pynithy" for base in [REPO] + list(REPO.parents)]
    return next((c for c in candidates if (c / ".git").exists()), None)


def code(text):
    """`text` without its comment lines that hold a print call."""
    return "\n".join(line for line in text.split("\n") if not re.match(r"^\s*#\s*print\(", line))


def functions_of(text):
    """The methods of the module's class by name, each its lines from its
    decorator to the line before the next line indented four spaces or less
    (the next member, a class-level comment or assignment, the class's end),
    trailing white space removed."""
    lines = [re.sub(r"[ \t]+$", "", line) for line in text.split("\n")]
    found = {}
    i = 0
    while i < len(lines):
        m = re.match(r"^    def (\w+)\(", lines[i])
        if not m:
            i += 1
            continue
        start = i - 1 if i > 0 and lines[i - 1] == "    @staticmethod" else i
        j = i + 1
        while j < len(lines) and not re.match(r"^ {0,4}\S", lines[j]):
            j += 1
        found[m.group(1)] = "\n".join(lines[start:j]).rstrip("\n")
        i = j
    return found


def test_every_function_the_copy_shares_with_the_trustedzone_is_the_trustedzone_s():
    found = checkout()
    if found is None:
        pytest.skip(f"no etny-pynithy checkout found (set {ENV})")
    shown = subprocess.run(["git", "-C", str(found), "show", f"{COMMIT}:{SOURCE}"], capture_output=True)
    assert shown.returncode == 0, f"{found} holds no {COMMIT}:{SOURCE}"
    theirs = functions_of(code(shown.stdout.decode("utf-8")))
    ours = functions_of(code(COPY.read_text(encoding="utf-8")))
    shared = [name for name in ours if name in theirs]
    assert len(shared) > 10
    for name in shared:
        assert ours[name] == theirs[name], name
    assert sorted(name for name in ours if name not in theirs) == OWN


def test_the_copy_holds_no_statement_signer_and_its_header_says_so():
    text = COPY.read_text(encoding="utf-8")
    for name, body in functions_of(text).items():
        assert "ecld-stmt-1" not in body, name
    header = text[:text.index("from __future__")]
    assert "statement signer" in header and '"ecld-stmt-1"' in header and "not in this copy" in header
