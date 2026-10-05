"""The securelock takes only the order id from the node-supplied bucket .env;
its chain, PoX, Image Registry and Web3 provider are build constants.

read_env_str and NODE_ENV_ALLOWLIST are taken from the template's own source
(the template does not import outside an enclave build), so these tests
exercise the code that is baked into the securelock."""
import ast
import io
import logging
import os
import re
from pathlib import Path

TEMPLATE = (Path(__file__).resolve().parent.parent / "ethernity_cloud_sdk_py" / "commands" / "pynithy"
            / "build" / "securelock" / "src" / "securelock.py.tmpl")

TRUST_ROOT_KEYS = ("ETNY_CHAIN_ID", "ETNY_SMART_CONTRACT_ADDRESS", "ETNY_WEB3_PROVIDER", "IMAGE_REGISTRY_ADDRESS")


def _securelock_class():
    tree = ast.parse(TEMPLATE.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "EtnySecureLock")
    return cls


def _env_reader():
    """EtnySecureLock with only NODE_ENV_ALLOWLIST and read_env_str, compiled
    from the template."""
    cls = _securelock_class()
    kept = [n for n in cls.body
            if (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "NODE_ENV_ALLOWLIST" for t in n.targets))
            or (isinstance(n, ast.FunctionDef) and n.name == "read_env_str")]
    assert len(kept) == 2, "the template defines NODE_ENV_ALLOWLIST and read_env_str"
    module = ast.Module(body=[ast.ClassDef(name="EtnySecureLock", bases=[], keywords=[], body=kept,
                                           decorator_list=[], type_params=[])], type_ignores=[])
    namespace = {"io": io, "os": os, "re": re, "logging": logging}
    exec(compile(ast.fix_missing_locations(module), str(TEMPLATE), "exec"), namespace)
    return namespace["EtnySecureLock"]


def test_only_the_order_id_comes_from_the_node_env(monkeypatch):
    for key in TRUST_ROOT_KEYS + ("ESR_CONTRACT_ADDRESS", "ECAS_CAS_QUOTE", "ETNY_ORDER_ID"):
        monkeypatch.delenv(key, raising=False)
    node_env = "\n".join([
        "ETNY_CHAIN_ID=1",
        "ETNY_SMART_CONTRACT_ADDRESS=0x000000000000000000000000000000000000dEaD",
        "ETNY_WEB3_PROVIDER=http://attacker.example",
        "IMAGE_REGISTRY_ADDRESS=0x000000000000000000000000000000000000bEEF",
        "ESR_CONTRACT_ADDRESS=0x0000000000000000000000000000000000000001",
        "ECAS_CAS_QUOTE=AAAA",
        "ETNY_ORDER_ID=1969",
    ])
    _env_reader().read_env_str(node_env)
    assert os.environ.get("ETNY_ORDER_ID") == "1969"
    for key in TRUST_ROOT_KEYS + ("ESR_CONTRACT_ADDRESS", "ECAS_CAS_QUOTE"):
        assert key not in os.environ, key


def test_the_node_env_never_overrides_the_environment(monkeypatch):
    monkeypatch.setenv("ETNY_ORDER_ID", "7")
    _env_reader().read_env_str("ETNY_ORDER_ID=8\n")
    assert os.environ["ETNY_ORDER_ID"] == "7"


def test_load_env_reads_no_trust_root_from_the_environment():
    cls = _securelock_class()
    load_env = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__load_env")
    names = {n.value for n in ast.walk(load_env) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for key in TRUST_ROOT_KEYS:
        assert key not in names, key
    assert "ETNY_ORDER_ID" in names
