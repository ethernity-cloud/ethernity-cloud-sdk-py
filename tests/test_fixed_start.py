"""The securelock's fixed start: build/securelock/fixed-start/fixed_start.c
with the securelock's header securelock.h, linked into the extension
Dockerfile.tpl builds.

The header's argument vector is the image's ENTRYPOINT (the interpreter
isolated, -I) and its fixed variable HOME=/tmp; its keys are the ones the
securelock's Python reads from its environment; securelock.py puts its own
directory on sys.path before it imports a sibling module. The Dockerfile
ecld-build renders links the launcher into /libbinary-fs.so; the build
directory holds fixed-start/; the freeze stage compiles site-packages with
hash-based bytecode headers. With a musl C library and gcc on the host, the
launcher is compiled with the securelock's header and a program started
through it runs with the fixed vector and environment whatever it is started
with."""
import ast
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ethernity_cloud_sdk_py.commands.pynithy import build

ROOT = Path(__file__).resolve().parent.parent
SECURELOCK = ROOT / "ethernity_cloud_sdk_py" / "commands" / "pynithy" / "build" / "securelock"
SRC = SECURELOCK / "src"
TEMPLATE = SRC / "securelock.py.tmpl"
DOCKERFILE = SECURELOCK / "Dockerfile.tpl"
PYFREEZE = SECURELOCK / "scripts" / "pyfreeze.sh"
LAUNCHER = SECURELOCK / "fixed-start" / "fixed_start.c"
HEADER = SECURELOCK / "fixed-start" / "securelock.h"
# The etny-nodenithy checkout: ETNY_NODENITHY, else the sibling of this repository.
NODENITHY = Path(os.environ.get("ETNY_NODENITHY") or ROOT.parent / "etny-nodenithy").resolve()
NODENITHY_LAUNCHER = NODENITHY / "v3" / "build" / "trustedzone" / "fixed-start" / "fixed_start.c"

VECTOR = ["/usr/local/bin/python", "-I", "/etny-securelock/securelock.py"]
FIXED_ENV = ["HOME=/tmp"]
ALLOWED = ["ETNY_ORDER_ID", "ECAS_ATTEST_SESSION", "ECAS_ENCLAVE_NONCE", "ECAS_CAS_QUOTE", "ESR_CONTRACT_ADDRESS"]
# Read by esr_wallet.py and not passed (securelock.h).
NOT_PASSED = ["ESR_IDENTITY_SECRET"]
SPACE = 32768
REFUSED = 78
LINK = ("RUN cd /binary-fs-dir && scone gcc ./binary_fs_blob.s -O2 -fPIC -include /fixed-start/securelock.h "
        "/fixed-start/fixed_start.c ./libbinary_fs_template.a -shared -o /libbinary-fs.so")
NO_ESR = {"enabled": False, "contract_address": "", "wallet_address": ""}


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def _literals(body):
    return [re.sub(r"\\(.)", lambda e: "\0" if e.group(1) == "0" else e.group(1), m.group(1))
            for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', body)]


def _macro(text, name):
    m = re.search(rf"^#define {name}\b(.*)$", re.sub(r"\\\r?\n", " ", text), re.M)
    assert m, f"the header defines {name}"
    return m.group(1)


def _fixed_start(text):
    return {
        "args": "".join(_literals(_macro(text, "FIXED_START_ARGS"))).split("\0"),
        "env": _literals(_macro(text, "FIXED_START_ENV")),
        "allowed": _literals(_macro(text, "FIXED_START_ALLOWED")),
    }


def _stages(dockerfile):
    return [s for s in re.split(r"^(?=FROM )", dockerfile, flags=re.M) if s.startswith("FROM ")]


def test_the_vector_is_the_isolated_interpreter_with_securelock_py_and_is_the_entrypoint():
    assert _fixed_start(_read(HEADER))["args"] == VECTOR
    entrypoint = re.search(r"^ENTRYPOINT (\[.*\])\s*$", _read(DOCKERFILE), re.M)
    assert json.loads(entrypoint.group(1)) == VECTOR


def test_home_is_set_whatever_the_start_holds_and_the_image_sets_it_too():
    assert _fixed_start(_read(HEADER))["env"] == FIXED_ENV
    assert re.search(r"^ENV HOME=/tmp$", _stages(_read(DOCKERFILE))[-1], re.M)


def test_the_keys_passed_through_are_the_keys_the_securelock_reads_but_esr_identity_secret():
    keys = set()
    pattern = re.compile(r"""os\.(?:getenv\(|environ\.get\(|environ\[)\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]""")
    for path in [p for p in SRC.iterdir() if p.suffix == ".py" or p.name.endswith(".py.tmpl")]:
        keys.update(pattern.findall(_read(path)))
    # etny_exec.py reads its required build values through os.environ[name].
    required = re.search(r"required = \(([^)]*)\)", _read(SRC / "etny_exec.py"))
    assert required
    keys.update(re.findall(r'"([A-Z_0-9]+)"', required.group(1)))
    # read_env_str() admits these keys from the bucket .env into os.environ.
    allowlist = re.search(r"NODE_ENV_ALLOWLIST = frozenset\(\{([^}]*)\}\)", _read(TEMPLATE))
    assert allowlist
    keys.update(re.findall(r"'([A-Z_0-9]+)'", allowlist.group(1)))
    allowed = _fixed_start(_read(HEADER))["allowed"]
    assert allowed == ALLOWED
    assert sorted(keys) == sorted(ALLOWED + NOT_PASSED)
    assert not set(NOT_PASSED) & set(allowed)


def test_securelock_puts_its_own_directory_on_sys_path_before_it_imports_a_sibling_module():
    tree = ast.parse(_read(TEMPLATE))
    siblings = {p.stem for p in SRC.glob("*.py")}
    insert = first_sibling = None
    for i, node in enumerate(tree.body):
        if insert is None and isinstance(node, ast.Expr) and ast.unparse(node) == \
                "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))":
            insert = i
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        if first_sibling is None and siblings & set(names):
            first_sibling = i
    assert insert is not None and first_sibling is not None
    assert insert < first_sibling


def test_one_line_links_the_launcher_into_the_extension_after_the_copy_of_fixed_start():
    dockerfile = _read(DOCKERFILE)
    writers = [line for line in dockerfile.split("\n") if re.search(r"-o /libbinary-fs\.so\b", line)]
    assert writers == [LINK]
    (binfs,) = [s for s in _stages(dockerfile) if LINK in s]
    assert re.match(r"FROM \S*sconecuratedimages/crosscompilers:\S+ AS binfs\n", binfs)
    copy = binfs.find("COPY ./fixed-start/ /fixed-start/\n")
    assert 0 <= copy < binfs.find(LINK)
    signed = _stages(dockerfile)[-1]
    assert re.search(r"^COPY --from=binfs /libbinary-fs\.so /lib/libbinary-fs\.so$", signed, re.M)
    assert re.search(r"^ENV SCONE_EXTENSIONS_PATH=/lib/libbinary-fs\.so$", signed, re.M)


@pytest.mark.parametrize("network,image", [
    ("BLOXBERG_TESTNET_UNSAFE", "etny-pynithy-testnet-unsafe"),
    ("BLOXBERG_TESTNET", "etny-pynithy-testnet"),
    ("POLYGON_AMOY", "ecld-pynithy-amoy"),
])
@pytest.mark.parametrize("production", [False, True])
def test_the_rendered_dockerfile_links_the_launcher_and_leaves_no_token(network, image, production):
    dockerfile = build.render_securelock_dockerfile(_read(DOCKERFILE), network, "PROJECT_SECURELOCK_V3_X_0_0_1", image,
                                                    "1G", NO_ESR, production)
    lines = dockerfile.split("\n")
    assert [line for line in lines if re.search(r"__[A-Z][A-Z0-9_]*__", line)] == []
    assert LINK in lines
    assert [line for line in lines if line.startswith("RUN scone-signer sign ")] == [
        "RUN scone-signer sign --key=/enclave-key.pem --env --heap=1G --stack=4M --tcs=64 --dlopen=1 "
        f"--extensions=/lib/libbinary-fs.so{' --production' if production else ''} /usr/local/bin/python"]
    assert "ENV SCONE_ALLOW_DLOPEN=1" in lines
    assert f"ENV BUCKET_NAME={image}-v3" in lines
    assert "ENV SECURELOCK_SESSION=PROJECT_SECURELOCK_V3_X_0_0_1" in lines


def test_the_rendered_dockerfile_bakes_the_esr_registry_only_when_esr_is_enabled():
    esr = {"enabled": True, "contract_address": "0x0Ea1728EAE108FD3B9340ae91451348E2Cc6b4E4", "wallet_address": ""}
    lines = build.render_securelock_dockerfile(_read(DOCKERFILE), "BLOXBERG_TESTNET_UNSAFE", "S",
                                               "etny-pynithy-testnet-unsafe", "1G", esr, False).split("\n")
    assert "ENV ESR_CONTRACT_ADDRESS=0x0Ea1728EAE108FD3B9340ae91451348E2Cc6b4E4" in lines
    lines = build.render_securelock_dockerfile(_read(DOCKERFILE), "BLOXBERG_TESTNET_UNSAFE", "S",
                                               "etny-pynithy-testnet-unsafe", "1G", NO_ESR, False).split("\n")
    assert not [line for line in lines if line.startswith("ENV ESR_CONTRACT_ADDRESS=")]


def test_the_base_dockerfile_names_the_networks_base_image():
    rendered = build.render_base_dockerfile(_read(SECURELOCK / "Dockerfile.base.tpl"), "registry.example/apps", "tag-1")
    assert rendered.startswith("FROM registry.example/apps:tag-1 AS release\n")


def test_the_build_directory_holds_the_fixed_start(tmp_path):
    assert build.copy_from_module_to_build_dir(tmp_path) is True
    copied = tmp_path / "securelock" / "fixed-start"
    assert sorted(p.name for p in copied.iterdir()) == ["fixed_start.c", "securelock.h"]
    assert (copied / "fixed_start.c").read_bytes() == LAUNCHER.read_bytes()
    assert (copied / "securelock.h").read_bytes() == HEADER.read_bytes()


def test_fixed_start_c_is_byte_identical_to_etny_nodenithys_when_that_checkout_is_present():
    if not NODENITHY_LAUNCHER.exists():
        pytest.skip(f"no etny-nodenithy checkout at {NODENITHY} (set ETNY_NODENITHY)")
    lf = lambda p: _read(p).replace("\r\n", "\n")  # noqa: E731
    assert lf(LAUNCHER) == lf(NODENITHY_LAUNCHER)


COMPILE_LINE = re.compile(r"^python -m compileall (.*) /usr/local/lib/python3\.14/site-packages \\$")


def _compileall_arguments():
    """The arguments pyfreeze.sh gives compileall."""
    (line,) = [m.group(1) for m in map(COMPILE_LINE.match, (x.strip() for x in _read(PYFREEZE).split("\n"))) if m]
    return line.split()


def test_the_freeze_stage_compiles_site_packages_with_unchecked_hash_headers_after_pyinstaller():
    lines = [line.strip() for line in _read(PYFREEZE).split("\n")]
    freeze = lines.index("pyinstaller --onedir --noconfirm securelock.py")
    remove = lines.index("find /usr/local/lib/python3.14/site-packages /etny-securelock -depth -type d "
                         "-name __pycache__ -exec rm -rf {} \\;")
    (compile_,) = [i for i, line in enumerate(lines) if COMPILE_LINE.match(line)]
    assert freeze < remove < compile_
    assert _compileall_arguments() == ["-q", "-f", "--invalidation-mode", "unchecked-hash"]


def _tree(root, mtime):
    """A package tree whose bytecode is written as pip writes it (timestamp
    headers), with every source's modification time set to mtime."""
    for rel, text in (("pkg/__init__.py", "X = 1\n"), ("pkg/mod.py", "def f():\n    return 'a'\n"),
                      ("pkg/sub/__init__.py", ""), ("top.py", "import pkg\n")):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (mtime, mtime))
        py_compile.compile(str(path), invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP, doraise=True)


def _bytecode(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.pyc"))}


def test_the_freeze_stage_s_recompilation_writes_the_same_bytecode_for_sources_of_other_times(tmp_path):
    # Two builds at the same path (the bytecode names its source's path), the
    # sources written at other times. Between the removal of the bytecode and
    # the compilation, pkg/mod.py gets the timestamped bytecode an import at
    # the compiling interpreter's start writes.
    root = tmp_path / "site-packages"
    builds = []
    for mtime in (1_000_000_000, 1_700_000_000):
        _tree(root, mtime)
        timestamped = _bytecode(root)
        for cache in sorted(root.rglob("__pycache__"), reverse=True):
            shutil.rmtree(cache)
        py_compile.compile(str(root / "pkg" / "mod.py"), invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
                           doraise=True)
        subprocess.run([sys.executable, "-m", "compileall", *_compileall_arguments(), str(root)], check=True)
        builds.append((timestamped, _bytecode(root)))
        shutil.rmtree(root)
    assert builds[0][0] != builds[1][0], "the timestamp headers differ"
    assert builds[0][1] == builds[1][1]
    assert len(builds[0][1]) == 4
    for data in builds[0][1].values():
        assert int.from_bytes(data[4:8], "little") == 0b01, "the header's flags: hash-based, not checked"


# A musl C library (the one SCONE's runtime is built on) and gcc.
def _toolchain_missing():
    if sys.platform != "linux":
        return f"the host is {sys.platform}, not Linux"
    if not any(re.match(r"ld-musl-.*\.so\.1$", f) for f in os.listdir("/lib")):
        return "the host C library is not musl"
    if shutil.which("gcc") is None:
        return "gcc is not on PATH"
    return None


STARTED_C = r"""#include <stdio.h>
#include <string.h>
extern char **environ;
int main(int argc, char **argv, char **envp)
{
    int i;
    int contiguous = 1;
    char **e;
    for (i = 0; i < argc; i++) {
        printf("arg %s\n", argv[i]);
        if (i > 0 && argv[i] != argv[i - 1] + strlen(argv[i - 1]) + 1)
            contiguous = 0;
    }
    for (e = environ; *e != NULL; e++)
        printf("env %s\n", *e);
    printf("envp %s\n", envp == environ ? "environ" : "other");
    printf("contiguous %d\n", contiguous);
    return 0;
}
"""

LOADER_C = r"""#include <dlfcn.h>
#include <stdio.h>
int main(int argc, char **argv)
{
    (void)argc;
    if (dlopen(argv[1], RTLD_NOW) == NULL)
        return 2;
    printf("loaded\n");
    return 0;
}
"""


@pytest.fixture(scope="module")
def launcher_build(tmp_path_factory):
    missing = _toolchain_missing()
    if missing:
        pytest.skip(missing)
    d = tmp_path_factory.mktemp("fixed-start")
    (d / "started.c").write_text(STARTED_C)
    (d / "loader.c").write_text(LOADER_C)
    lib = d / "libfixedstart.so"

    def gcc(*args):
        r = subprocess.run(["gcc", *map(str, args)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    gcc("-shared", "-fPIC", "-O2", "-Wall", "-Wextra", "-Werror", "-include", HEADER, "-o", lib, LAUNCHER)
    gcc("-O2", "-o", d / "started", d / "started.c", f"-L{d}", "-lfixedstart", f"-Wl,-rpath,{d}")
    gcc("-O2", "-o", d / "loader", d / "loader.c")
    return {"lib": lib, "started": d / "started", "loader": d / "loader"}


def _lines(out, tag):
    return [line[len(tag) + 1:] for line in out.split("\n") if line.startswith(f"{tag} ")]


def test_a_program_started_through_the_launcher_runs_the_fixed_vector_with_home_and_the_listed_keys(launcher_build):
    r = subprocess.run([launcher_build["started"], "-c", "import os"], capture_output=True, text=True,
                       env={"PYTHONPATH": "/etc", "PYTHONINSPECT": "1", "ETNY_ORDER_ID": "7", "ETNY_MODE": "PUBLIC_KEY",
                            "ESR_IDENTITY_SECRET": "1", "ESR_CONTRACT_ADDRESS": "0x0Ea1728EAE108FD3B9340ae91451348E2Cc6b4E4",
                            "HOME": "/root"})
    assert r.returncode == 0, r.stderr
    assert _lines(r.stdout, "arg") == VECTOR
    assert _lines(r.stdout, "env") == ["HOME=/tmp", "ETNY_ORDER_ID=7",
                                       "ESR_CONTRACT_ADDRESS=0x0Ea1728EAE108FD3B9340ae91451348E2Cc6b4E4"]
    assert _lines(r.stdout, "envp") == ["environ"]
    assert _lines(r.stdout, "contiguous") == ["1"]


def test_every_listed_key_is_passed_with_its_value_as_it_is_up_to_the_launchers_space(launcher_build):
    env = {key: f"value {i} = -c x" for i, key in enumerate(ALLOWED)}
    r = subprocess.run([launcher_build["started"]], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert _lines(r.stdout, "env") == FIXED_ENV + [f"{key}=value {i} = -c x" for i, key in enumerate(ALLOWED)]
    over = subprocess.run([launcher_build["started"]], capture_output=True, text=True, env={"ECAS_CAS_QUOTE": "A" * SPACE})
    assert over.returncode == REFUSED
    assert over.stderr == "fixed start: the start is refused: the environment exceeds the launcher's space\n"


def test_a_start_that_did_not_come_through_the_launcher_is_refused_when_its_object_is_loaded(launcher_build):
    r = subprocess.run([launcher_build["loader"], launcher_build["lib"]], capture_output=True, text=True)
    assert r.returncode == REFUSED
    assert r.stdout == ""
    assert r.stderr == "fixed start: the start is refused: the start did not come through the launcher\n"
