"""The securelock's fixed start: build/securelock/fixed-start/fixed_start.c
with the securelock's header securelock.h, linked into the extension
Dockerfile.tpl builds.

The header's argument vector is the image's ENTRYPOINT (the interpreter
isolated, -I) and its fixed variable HOME=/tmp; its keys are the ones the
securelock's Python reads from its environment; securelock.py puts its own
directory on sys.path before it imports a sibling module. The Dockerfile
ecld-build renders links the launcher into /libbinary-fs.so; the build
directory holds fixed-start/; the freeze stage compiles site-packages with
hash-based bytecode headers. The file tree check: fixed-start/securelock.tree
names the paths from which the interpreter loads code and the files its path
configuration opens by name, the binfs stage writes their digests with
fixed-start/tree_digest.sh after it makes the binary-fs, and the link line
compiles them into the launcher. With a musl C library, gcc and bash on the
host, the launcher is compiled with the securelock's header and the digests of
a tree, and a program started through it runs with the fixed vector and
environment whatever it is started with, and only while that tree is the one
the digests were written for."""
import ast
import hashlib
import json
import os
import py_compile
import re
import shutil
import stat
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
TREE_FILE = SECURELOCK / "fixed-start" / "securelock.tree"
TREE_DIGEST = SECURELOCK / "fixed-start" / "tree_digest.sh"
BINARY_FS_BUILD = SECURELOCK / "scripts" / "binary-fs-build.sh"
# The etny-nodenithy checkout: ETNY_NODENITHY, else the sibling of this repository.
NODENITHY = Path(os.environ.get("ETNY_NODENITHY") or ROOT.parent / "etny-nodenithy").resolve()
NODENITHY_FIXED_START = NODENITHY / "v3" / "build" / "trustedzone" / "fixed-start"
NODENITHY_LAUNCHER = NODENITHY_FIXED_START / "fixed_start.c"

VECTOR = ["/usr/local/bin/python", "-I", "/etny-securelock/securelock.py"]
# The paths the file tree check covers (securelock.tree): the entry's
# directory and the standard library's (sys.path), and what the tree does not
# hold: the zip archive on sys.path and the names getpath and site read beside
# the interpreter (a ._pth file, pyvenv.cfg, the build marker, the standard
# library's landmarks searched up to /usr).
TREE = [
    "/etny-securelock", "/usr/local/lib/python3.14", "/usr/local/lib/python314.zip",
    "/usr/local/bin/python._pth", "/usr/local/bin/pyvenv.cfg", "/usr/local/pyvenv.cfg",
    "/usr/local/bin/pybuilddir.txt", "/usr/local/bin/lib", "/usr/lib/python314.zip", "/usr/lib/python3.14",
]
FIXED_ENV = ["HOME=/tmp"]
ALLOWED = ["ETNY_ORDER_ID", "ECAS_ATTEST_SESSION", "ECAS_ENCLAVE_NONCE", "ECAS_CAS_QUOTE", "ESR_CONTRACT_ADDRESS"]
# Read by esr_wallet.py and not passed (securelock.h).
NOT_PASSED = ["ESR_IDENTITY_SECRET"]
SPACE = 32768
REFUSED = 78
LINK = ("RUN cd /binary-fs-dir && scone gcc ./binary_fs_blob.s -O2 -fPIC -include /fixed-start/securelock.h "
        "-include /binary-fs-dir/fixed_start_tree.h /fixed-start/fixed_start.c ./libbinary_fs_template.a -shared "
        "-o /libbinary-fs.so")
TREE_RUN = ("RUN bash /fixed-start/tree_digest.sh /fixed-start/securelock.tree /binary-fs-dir/fixed_start_tree.h "
            "/binary-fs-dir/fixed_start_tree.txt")
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


def test_the_binfs_stage_writes_the_tree_s_digests_after_it_makes_the_binary_fs_and_before_the_link():
    (binfs,) = [s for s in _stages(_read(DOCKERFILE)) if TREE_RUN in s]
    make = binfs.find("/etny-securelock/binary-fs-build.sh\n")
    copy = binfs.find("COPY ./fixed-start/ /fixed-start/\n")
    run = binfs.find(TREE_RUN)
    assert 0 <= make < copy < run < binfs.find(LINK)
    # Nothing after the digests changes the file system the binary-fs was made from.
    assert [line for line in binfs[run:].split("\n") if re.match(r"(RUN|COPY|ADD) ", line)] == [TREE_RUN, LINK]
    # The binary-fs holds the two directories of the tree whole.
    script = _read(BINARY_FS_BUILD)
    for directory in TREE[:2]:
        assert f"--include '{directory}/*'" in script


def _tree_paths(text):
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def test_the_tree_file_names_the_entry_s_directory_the_standard_library_and_the_names_getpath_reads():
    assert _tree_paths(_read(TREE_FILE)) == TREE
    assert TREE[0] == os.path.dirname(VECTOR[2])
    interpreter = VECTOR[0]
    bin_dir = os.path.dirname(interpreter)
    prefix = os.path.dirname(bin_dir)
    assert TREE[1] == f"{prefix}/lib/python3.14"
    assert TREE[2] == f"{prefix}/lib/python314.zip"
    assert TREE[3] == f"{interpreter}._pth"
    assert TREE[4:7] == [f"{bin_dir}/pyvenv.cfg", f"{prefix}/pyvenv.cfg", f"{bin_dir}/pybuilddir.txt"]
    assert TREE[7:] == [f"{bin_dir}/lib", f"{os.path.dirname(prefix)}/lib/python314.zip",
                        f"{os.path.dirname(prefix)}/lib/python3.14"]


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
    assert sorted(p.name for p in copied.iterdir()) == ["fixed_start.c", "securelock.h", "securelock.tree",
                                                        "tree_digest.sh"]
    for name in ("fixed_start.c", "securelock.h", "securelock.tree", "tree_digest.sh"):
        assert (copied / name).read_bytes() == (SECURELOCK / "fixed-start" / name).read_bytes()


def test_fixed_start_c_and_tree_digest_sh_are_byte_identical_to_etny_nodenithys_when_that_checkout_is_present():
    if not NODENITHY_LAUNCHER.exists():
        pytest.skip(f"no etny-nodenithy checkout at {NODENITHY} (set ETNY_NODENITHY)")
    lf = lambda p: _read(p).replace("\r\n", "\n")  # noqa: E731
    assert lf(LAUNCHER) == lf(NODENITHY_LAUNCHER)
    assert lf(TREE_DIGEST) == lf(NODENITHY_FIXED_START / "tree_digest.sh")


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


# A musl C library (the one SCONE's runtime is built on), gcc, and bash for
# tree_digest.sh.
def _toolchain_missing():
    if sys.platform != "linux":
        return f"the host is {sys.platform}, not Linux"
    if not any(re.match(r"ld-musl-.*\.so\.1$", f) for f in os.listdir("/lib")):
        return "the host C library is not musl"
    if shutil.which("gcc") is None:
        return "gcc is not on PATH"
    if shutil.which("bash") is None:
        return "bash is not on PATH"
    return None


def _tree_digest(p):
    """The digest of the tree at p as fixed_start.c defines it, computed here
    from the definition."""
    h = hashlib.sha256()

    def record(*fields):
        for field in fields:
            h.update(os.fsencode(field) + b"\0")

    def entry(x):
        st = os.lstat(x)
        if stat.S_ISREG(st.st_mode):
            data = Path(x).read_bytes()
            record("F" + x, str(len(data)), hashlib.sha256(data).hexdigest())
        elif stat.S_ISLNK(st.st_mode):
            record("L" + x, os.readlink(x))
        elif stat.S_ISDIR(st.st_mode):
            for name in sorted(os.listdir(x), key=os.fsencode):
                entry(f"{x}/{name}")
        else:
            record("O" + x)

    if os.path.lexists(p):
        entry(p)
    else:
        record("A" + p)
    return h.hexdigest()


def _make_tree(root, reverse=False, mode=0o644, mtime=1_000_000_000):
    """A tree for the launcher's tests under root: the entry, the dApp's
    serverless package, a json package, names whose bytewise order differs
    from a sort of the whole paths (pkg/, pkg-b.py, pkg.py), a .pth file and a
    symbolic link. reverse reverses the order the entries are made in; mode and
    mtime set the files' modes and times."""
    files = [("securelock.py", "import serverless.backend\n"), ("serverless/backend.py", "def hello(m):\n    return m\n"),
             ("pkg/__init__.py", "X = 1\n"), ("pkg-b.py", "B = 1\n"), ("pkg.py", "import pkg\n"),
             ("json/__init__.py", "dumps = repr\n"), ("site-packages/dist.pth", "./pkg\n")]
    steps = []
    for rel, text in files:
        def write(rel=rel, text=text):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            os.chmod(path, mode)
            os.utime(path, (mtime, mtime))
        steps.append(write)
    steps.append(lambda: os.symlink("pkg.py", root / "link.py"))
    root.mkdir(parents=True, exist_ok=True)
    for step in (reversed(steps) if reverse else steps):
        step()


def _digests(tree_file, out):
    """tree_digest.sh over tree_file: its header and its listing."""
    r = subprocess.run(["bash", str(TREE_DIGEST), str(tree_file), f"{out}.h", f"{out}.txt"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return _read(f"{out}.h"), _read(f"{out}.txt")


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
    tree = d / "etny-securelock"
    absent = d / "python314.zip"
    tree_file = d / "test.tree"
    _make_tree(tree)
    tree_file.write_text(f"# the test's paths\n{tree}\n{absent}\n")
    _digests(tree_file, d / "fixed_start_tree")

    def gcc(*args):
        r = subprocess.run(["gcc", *map(str, args)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    gcc("-shared", "-fPIC", "-O2", "-Wall", "-Wextra", "-Werror", "-include", HEADER,
        "-include", d / "fixed_start_tree.h", "-o", lib, LAUNCHER)
    gcc("-O2", "-o", d / "started", d / "started.c", f"-L{d}", "-lfixedstart", f"-Wl,-rpath,{d}")
    gcc("-O2", "-o", d / "loader", d / "loader.c")
    return {"dir": d, "lib": lib, "started": d / "started", "loader": d / "loader", "tree": tree,
            "absent": absent, "tree_file": tree_file}


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


def _start(launcher_build):
    return subprocess.run([launcher_build["started"]], capture_output=True, text=True, env={"ETNY_ORDER_ID": "7"})


def _refused_at(launcher_build, where, change, undo):
    """A start through the launcher after change() is refused before main;
    once undone, the next start runs main."""
    change()
    try:
        r = _start(launcher_build)
        assert r.returncode == REFUSED, r.stdout
        assert r.stdout == ""
        assert r.stderr == f"fixed start: the start is refused: the file tree is not the image's at {where}\n"
    finally:
        undo()
    again = _start(launcher_build)
    assert again.returncode == 0, again.stderr


def test_the_generator_writes_the_digest_of_each_path_the_tree_file_names_as_fixed_start_c_defines_it(launcher_build):
    tree, absent = str(launcher_build["tree"]), str(launcher_build["absent"])
    header, listing = _digests(launcher_build["tree_file"], launcher_build["dir"] / "check")
    assert header == "\n".join([
        "/* Written by tree_digest.sh from test.tree: the digest of each path. */",
        "#define FIXED_START_TREE \\",
        f'    {{ "{tree}", "{_tree_digest(tree)}" }}, \\',
        f'    {{ "{absent}", "{_tree_digest(absent)}" }}, \\',
        "",
        "",
    ])
    entries = [line.split(" ")[-1] for line in listing.split("\n") if re.match(r"[FLOA] ", line)]
    assert entries == [f"{tree}/json/__init__.py", "pkg.py", f"{tree}/pkg/__init__.py", f"{tree}/pkg-b.py",
                       f"{tree}/pkg.py", f"{tree}/securelock.py", f"{tree}/serverless/backend.py",
                       f"{tree}/site-packages/dist.pth", absent]


def test_a_start_whose_tree_is_the_one_the_digests_were_written_for_runs_the_program_s_main(launcher_build):
    r = _start(launcher_build)
    assert r.returncode == 0, r.stderr
    assert _lines(r.stdout, "arg") == VECTOR


def test_a_changed_file_is_refused_the_entry_the_dapp_s_backend_and_a_standard_library_module(launcher_build):
    tree = launcher_build["tree"]
    for rel in ("securelock.py", "serverless/backend.py", "json/__init__.py"):
        path = tree / rel
        was = path.read_bytes()
        _refused_at(launcher_build, tree, lambda: path.write_text("import os\n"), lambda: path.write_bytes(was))


def test_an_added_file_is_refused_sitecustomize_a_pth_file_a_module_a_package_and_a_zip(launcher_build):
    tree, absent = launcher_build["tree"], launcher_build["absent"]
    for rel in ("sitecustomize.py", "site-packages/sitecustomize.py", "site-packages/added.pth", "json.py",
                "cryptography/__init__.py"):
        path = tree / rel

        def add(path=path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("import os\n")

        def undo(path=path, rel=rel):
            shutil.rmtree(tree / "cryptography") if rel.startswith("cryptography/") else path.unlink()

        _refused_at(launcher_build, tree, add, undo)
    _refused_at(launcher_build, absent, lambda: absent.write_bytes(b"PK\x05\x06" + b"\0" * 18), absent.unlink)


def test_the_digests_do_not_depend_on_modes_times_or_the_order_the_entries_were_made_in(launcher_build):
    tree, d = launcher_build["tree"], launcher_build["dir"]
    first = _digests(launcher_build["tree_file"], d / "first")
    assert _digests(launcher_build["tree_file"], d / "second") == first
    kept = d / "tree-kept"
    tree.rename(kept)
    try:
        _make_tree(tree, reverse=True, mode=0o600, mtime=2_000_000_000)
        assert _digests(launcher_build["tree_file"], d / "remade") == first
        assert _start(launcher_build).returncode == 0
    finally:
        shutil.rmtree(tree)
        kept.rename(tree)
