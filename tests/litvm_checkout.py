"""The pox-smart-contract-litvm checkout the tests read the spec round's
commit from, and `git show <commit>:<path>` in it."""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LITVM_ENV = "POX_SMART_CONTRACT_LITVM"
# The commit of pox-smart-contract-litvm the copies in this repository are
# taken from (spec/README.md "Pinning a copy").
SOURCE_COMMIT = "2f600f6"


def litvm_checkout():
    """POX_SMART_CONTRACT_LITVM, else the first pox-smart-contract-litvm
    beside this repository or beside a directory above it; None when none
    holds a .git."""
    candidates = [Path(os.environ[LITVM_ENV]).resolve()] if os.environ.get(LITVM_ENV) else []
    candidates += [base.parent / "pox-smart-contract-litvm" for base in [REPO] + list(REPO.parents)]
    return next((c for c in candidates if (c / ".git").exists()), None)


def shown_at(checkout, path, commit=SOURCE_COMMIT):
    """The bytes of `git show <commit>:<path>` in `checkout`; None when the
    checkout holds no such object."""
    shown = subprocess.run(["git", "-C", str(checkout), "show", f"{commit}:{path}"], capture_output=True)
    return shown.stdout if shown.returncode == 0 else None
