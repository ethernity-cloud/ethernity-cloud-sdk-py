"""commands/pynithy/run/image_registry.abi, the ABI ecld-publish calls the
image registry with (commands/pynithy/run/image_registry.py), is a
byte-identical copy of pox-smart-contract-litvm's abi/ECImageRegistryV4.json
at SOURCE_COMMIT, the file ethernity-cloud-sdk-js's nodenithy/run and
pynithy/run copies equal: every function, event and error of
ECImageRegistryV4, whose earlier registries' functions (registerImage,
setImageCert, addImage, addTrustedZoneImage, imageDetails, ...) it holds
under the same signatures. Each entry's selector or topic0 is the one
tests/spec/abi-selectors.json lists for ECImageRegistryV4.json, and that file
lists no entry the copy lacks."""
import json
from pathlib import Path

import pytest
from eth_utils import keccak

from tests.litvm_checkout import LITVM_ENV, SOURCE_COMMIT, litvm_checkout, shown_at

REPO = Path(__file__).resolve().parents[1]
COPY = REPO / "ethernity_cloud_sdk_py" / "commands" / "pynithy" / "run" / "image_registry.abi"
SOURCE = "abi/ECImageRegistryV4.json"
SELECTORS = json.loads((Path(__file__).resolve().parent / "spec" / "abi-selectors.json").read_text(encoding="utf-8"))
ROWS = [e for e in SELECTORS["entries"] if e["abi"] == "ECImageRegistryV4.json"]


def canonical(param):
    """The canonical ABI type of an input: a tuple as its components' types
    in parentheses, with its array suffix."""
    kind = param["type"]
    if kind.startswith("tuple"):
        return "(" + ",".join(canonical(c) for c in param["components"]) + ")" + kind[len("tuple"):]
    return kind


def selectors_of(abi):
    rows = []
    for entry in abi:
        if entry["type"] not in ("function", "event", "error"):
            continue
        signature = f"{entry['name']}({','.join(canonical(p) for p in entry['inputs'])})"
        digest = keccak(text=signature)
        value = "0x" + (digest.hex() if entry["type"] == "event" else digest[:4].hex())
        rows.append((entry["type"], entry["name"], signature, value))
    return sorted(rows)


def test_every_entry_has_the_selector_abi_selectors_lists_and_it_lists_none_the_abi_lacks():
    listed = sorted((e["kind"], e["name"], e["signature"], e["topic0"] if e["kind"] == "event" else e["selector"])
                    for e in ROWS)
    assert len(ROWS) == 92
    assert selectors_of(json.loads(COPY.read_text(encoding="utf-8"))) == listed


def test_the_functions_ecld_publish_calls_are_in_the_abi():
    names = {(e["type"], e.get("name")) for e in json.loads(COPY.read_text(encoding="utf-8"))}
    for name in ("registerImage", "setImageCert", "imageEpcUnits", "setImageEpcUnits", "seedingOpen", "imageDetails",
                 "getImageCertPublicKey", "imageNameOwner", "allowedWallets"):
        assert ("function", name) in names, name
    assert {("error", "SeedingOpen"), ("error", "ImageEpcRefused")} <= names


def test_the_abi_is_the_source_commit_s_ec_image_registry_v4():
    checkout = litvm_checkout()
    if checkout is None:
        pytest.skip(f"no pox-smart-contract-litvm checkout found (set {LITVM_ENV})")
    source = shown_at(checkout, SOURCE)
    assert source is not None, f"{checkout} holds no {SOURCE_COMMIT}:{SOURCE}"
    assert COPY.read_bytes() == source
