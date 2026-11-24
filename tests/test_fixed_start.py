"""The securelock's fixed start: build/securelock/fixed-start/fixed_start.c
with the securelock's header securelock.h, linked into the extension
Dockerfile.tpl builds.

The header's argument vector is the image's ENTRYPOINT (the interpreter
isolated, -I); its fixed variables are HOME=/tmp and an OpenSSL configuration,
module directory and engine directory inside the image (an empty file and the
directory that holds it alone); its keys are the ones the securelock's Python
reads from its environment; its working directory is the image's; the entries
it excepts are the runtime's device and process directories, the two host
files of the binary-fs and the files the CAS session places. securelock.py
puts its own directory on sys.path before it imports a sibling module.

The freeze stage installs the securelock's packages and the dApp's
requirements (no PyInstaller), compiles site-packages with hash-based bytecode
headers and assembles the enclave's files in a new directory, /enclave-root
(scripts/enclave-root.sh). The binfs stage makes the binary-fs of that root
whole, writes the root's digest with fixed-start/tree_digest.sh and compiles
it, with the session's name, into the launcher. The Dockerfile ecld-build
renders links the launcher into /libbinary-fs.so; the build directory holds
fixed-start/ and the three build scripts.

With a musl C library, gcc, bash and the right to chroot on the host, the
launcher is compiled with the securelock's header and the digest of a root,
and a program started through it inside that root runs with the fixed vector,
environment and working directory whatever it is started with, and only while
the root holds the entries the digest was written for and, beside them, the
excepted entries alone."""
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
PYNITHY = ROOT / "ethernity_cloud_sdk_py" / "commands" / "pynithy"
SECURELOCK = PYNITHY / "build" / "securelock"
SRC = SECURELOCK / "src"
TEMPLATE = SRC / "securelock.py.tmpl"
DOCKERFILE = SECURELOCK / "Dockerfile.tpl"
PYFREEZE = SECURELOCK / "scripts" / "pyfreeze.sh"
ENCLAVE_ROOT_SH = SECURELOCK / "scripts" / "enclave-root.sh"
BINARY_FS_BUILD = SECURELOCK / "scripts" / "binary-fs-build.sh"
FIXED_START = SECURELOCK / "fixed-start"
LAUNCHER = FIXED_START / "fixed_start.c"
HEADER = FIXED_START / "securelock.h"
TREE_DIGEST = FIXED_START / "tree_digest.sh"
SESSION_TEMPLATE = PYNITHY / "run" / "etny-securelock-test.yaml.tpl"
# The etny-nodenithy checkout: ETNY_NODENITHY, else the sibling of this repository.
NODENITHY = Path(os.environ.get("ETNY_NODENITHY") or ROOT.parent / "etny-nodenithy").resolve()
NODENITHY_FIXED_START = NODENITHY / "v3" / "build" / "trustedzone" / "fixed-start"
# The files of fixed-start/ that are one text in every enclave's build.
SHARED = ["fixed_start.c", "tree_digest.sh", "elf_needed.sh"]

ENTRY_DIR = "/etny-securelock"
OPENSSL_DIR = f"{ENTRY_DIR}/fixed-start"
OPENSSL_CONFIG = f"{OPENSSL_DIR}/openssl.cnf"
STDLIB = "/usr/local/lib/python3.14"
VECTOR = ["/usr/local/bin/python", "-I", f"{ENTRY_DIR}/securelock.py"]
FIXED_ENV = ["HOME=/tmp", f"OPENSSL_CONF={OPENSSL_CONFIG}", f"OPENSSL_MODULES={OPENSSL_DIR}",
             f"OPENSSL_ENGINES={OPENSSL_DIR}"]
WORKING_DIRECTORY = ENTRY_DIR
ALLOWED = ["ETNY_ORDER_ID", "ECAS_ATTEST_SESSION", "ECAS_ENCLAVE_NONCE", "ECAS_CAS_QUOTE", "ESR_CONTRACT_ADDRESS"]
# Read by esr_wallet.py and not passed (securelock.h).
NOT_PASSED = ["ESR_IDENTITY_SECRET"]
ENCLAVE_ROOT = "/enclave-root"
SPACE = 32768
DEPTH_MAX = 64
REFUSED = 78
SESSION_NAME = "PROJECT_SECURELOCK_V3_X_0_0_1"


def _link(session):
    return ("RUN cd /binary-fs-dir && scone gcc ./binary_fs_blob.s -O2 -fPIC "
            f"-DFIXED_START_SESSION='\"{session}\"' -include /fixed-start/securelock.h "
            "-include /binary-fs-dir/fixed_start_tree.h /fixed-start/fixed_start.c ./libbinary_fs_template.a -shared "
            "-o /libbinary-fs.so")


LINK = _link("__SECURELOCK_SESSION__")
TREE_RUN = (f"RUN bash /fixed-start/tree_digest.sh {ENCLAVE_ROOT} /binary-fs-dir/fixed_start_tree.h "
            "/binary-fs-dir/fixed_start_tree.txt")
NO_ESR = {"enabled": False, "contract_address": "", "wallet_address": ""}


def excepted(session):
    """The entries of the enclave's file system that are not the image's
    (securelock.h FIXED_START_EXCEPT), for a session of the name given: the
    kind, the path and, for a directory that lists names, the names."""
    return [
        ("S", "/dev", None),
        ("S", "/proc", None),
        ("N", "/etc", ["hosts", "resolv.conf"]),
        ("N", "/app", [session]),
        ("N", f"/app/{session}", ["ca.pem", "cert.pem"]),
        ("N", "/private", [session]),
        ("N", f"/private/{session}", ["key.pem"]),
    ]


def _read(path):
    return Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")


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
        "pwd": "".join(_literals(_macro(text, "FIXED_START_PWD"))),
    }


def _except_records(text, session):
    """The records of FIXED_START_EXCEPT, with `session` for FIXED_START_SESSION."""
    body = _macro(text, "FIXED_START_EXCEPT").replace("FIXED_START_SESSION", json.dumps(session))
    records = []
    for m in re.finditer(r"\{([^}]*)\}", body):
        kind, at, names = (field.strip() for field in m.group(1).split(","))
        assert re.fullmatch(r"'[A-Z]'", kind)
        records.append((kind[1], "".join(_literals(at)),
                        None if names == "NULL" else "".join(_literals(names)).split("\0")[:-1]))
    return records


def _stages(dockerfile):
    return [s for s in re.split(r"^(?=FROM )", dockerfile, flags=re.M) if s.startswith("FROM ")]


def _commands(script):
    """The lines of a shell script that are not comments, stripped."""
    return [line.strip() for line in script.split("\n") if line.strip() and not line.strip().startswith("#")]


def _steps(stage):
    """The RUN, COPY and ADD lines of a Dockerfile stage."""
    return [line for line in stage.split("\n") if re.match(r"(RUN|COPY|ADD) ", line)]


def test_the_vector_is_the_isolated_interpreter_with_securelock_py_and_is_the_entrypoint():
    header = _fixed_start(_read(HEADER))
    assert header["args"] == VECTOR
    entrypoint = re.search(r"^ENTRYPOINT (\[.*\])\s*$", _read(DOCKERFILE), re.M)
    assert json.loads(entrypoint.group(1)) == VECTOR


def test_home_and_the_openssl_variables_are_set_whatever_the_start_holds_and_the_image_sets_home_too():
    assert _fixed_start(_read(HEADER))["env"] == FIXED_ENV
    assert re.search(r"^ENV HOME=/tmp$", _stages(_read(DOCKERFILE))[-1], re.M)


def test_the_openssl_configuration_is_a_file_enclave_root_sh_makes_empty_alone_in_a_directory_of_the_entry_s():
    env = dict(entry.split("=", 1) for entry in FIXED_ENV)
    assert os.path.dirname(env["OPENSSL_CONF"]) == env["OPENSSL_MODULES"] == env["OPENSSL_ENGINES"]
    assert os.path.dirname(env["OPENSSL_MODULES"]) == os.path.dirname(VECTOR[2])
    lines = _commands(_read(ENCLAVE_ROOT_SH))
    made = lines.index(f': > "${{ROOT}}{OPENSSL_CONFIG}"')
    assert made > lines.index(f'mkdir "${{ROOT}}{OPENSSL_DIR}"')
    # No other line writes below the file's directory.
    assert len([line for line in lines if OPENSSL_DIR in line]) == 2


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
    insert = None
    first_sibling = None
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


def test_the_working_directory_is_the_entry_s_the_image_s_scone_pwd_and_workdir():
    assert _fixed_start(_read(HEADER))["pwd"] == WORKING_DIRECTORY == os.path.dirname(VECTOR[2])
    signed = _stages(_read(DOCKERFILE))[-1]
    assert re.search(rf"^ENV SCONE_PWD={WORKING_DIRECTORY}$", signed, re.M)
    assert re.search(rf"^WORKDIR {WORKING_DIRECTORY}$", signed, re.M)


def test_the_excepted_entries_are_dev_and_proc_unread_etc_with_the_two_host_files_and_the_session_s_directories():
    header = _read(HEADER)
    assert _except_records(header, "a-session") == excepted("a-session")
    assert re.search(r"^#if !defined\(FIXED_START_SESSION\)\n#error ", header, re.M)
    assert re.search(r"^_Static_assert\(sizeof FIXED_START_SESSION > 1, ", header, re.M)
    # /etc holds the host paths of the binary-fs.
    host_paths = sorted(re.findall(r"--host-path=([^\s)]+)", "\n".join(_commands(_read(BINARY_FS_BUILD)))))
    (etc,) = [names for _, at, names in excepted("s") if at == "/etc"]
    assert host_paths == sorted(f"/etc/{name}" for name in etc)
    # The session's directories hold the files the CAS session places, under
    # the name securelock.py reads its key and certificate by.
    placed = sorted(re.findall(r"^\s+- path: (\S+)\s*$", _read(SESSION_TEMPLATE), re.M))
    files = sorted(f"{at}/{name}" for kind, at, names in excepted("__ENCLAVE_NAME__")
                   if kind == "N" and at.count("/") == 2 for name in names)
    assert placed == files and len(placed) == 3
    template = _read(TEMPLATE)
    assert 'key_file = "/private/__SECURELOCK_SESSION__/key.pem"' in template
    assert 'cert_file = "/app/__SECURELOCK_SESSION__/cert.pem"' in template


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


def test_the_binfs_stage_makes_the_binary_fs_of_the_root_the_freeze_stage_assembled_writes_its_digest_and_links():
    (binfs,) = [s for s in _stages(_read(DOCKERFILE)) if TREE_RUN in s]
    # The stage takes the root and its ELF listing, makes the binary-fs,
    # reads the root for its digest and links: nothing else, in that order.
    assert _steps(binfs) == [
        f"COPY --from=pyfreeze {ENCLAVE_ROOT} {ENCLAVE_ROOT}",
        "COPY --from=pyfreeze /fixed_start_elf.txt /binary-fs-dir/fixed_start_elf.txt",
        f"RUN bash {ENCLAVE_ROOT}{ENTRY_DIR}/binary-fs-build.sh",
        "COPY ./fixed-start/ /fixed-start/",
        TREE_RUN,
        LINK,
    ]


def test_the_freeze_stage_assembles_the_root_after_its_packages_and_its_bytecode_are_written():
    (freeze,) = [s for s in _stages(_read(DOCKERFILE)) if re.match(r"FROM python:3\.14-alpine AS pyfreeze\n", s)]
    steps = _steps(freeze)
    scone_python = [i for i, step in enumerate(steps) if step.startswith("COPY --from=etny-securelock-serverless ")]
    assert [steps[i].split()[2:] for i in scone_python] == [
        ["/usr/local/bin/python3", "/scone-python/bin/python3"],
        [STDLIB, "/scone-python/lib/python3.14"],
    ]
    compiled = steps.index(f"RUN chmod +x {ENTRY_DIR}/pyfreeze.sh && {ENTRY_DIR}/pyfreeze.sh")
    requirements = steps.index("RUN pip install --no-cache-dir -r /requirements.txt || true")
    assert requirements < compiled < scone_python[0]
    assert steps[scone_python[1] + 1:] == ["COPY ./fixed-start/ /fixed-start/", f"RUN bash {ENTRY_DIR}/enclave-root.sh"]


def test_enclave_root_sh_assembles_the_enclave_s_files_in_a_new_root_and_lists_its_elf_files():
    lines = _commands(_read(ENCLAVE_ROOT_SH))
    assert lines[0] == "set -euo pipefail"
    root = lines.index(f"ROOT={ENCLAVE_ROOT}")
    assert lines.index(f"STDLIB={STDLIB}") == root + 1
    empty = lines.index('find "${ROOT}" -depth -type d -empty -exec rmdir {} \\;')
    # Every command on the root, in order: the first makes it and fails when it exists.
    assert [line for line in lines[root + 1:empty] if "${ROOT}" in line] == [
        'mkdir "${ROOT}"',
        'mkdir -p "${ROOT}/usr/local/bin" "${ROOT}/usr/local/lib"',
        'cp /scone-python/bin/python3 "${ROOT}/usr/local/bin/python3"',
        'cp -a /scone-python/lib/python3.14 "${ROOT}${STDLIB}"',
        'rm -rf "${ROOT}${STDLIB}/site-packages"',
        'find "${ROOT}${STDLIB}" -name libpython3.14.a -exec rm {} \\;',
        'cp -a "${STDLIB}/site-packages" "${ROOT}${STDLIB}/site-packages"',
        'rm -rf "${ROOT}${STDLIB}/site-packages/pip" "${ROOT}${STDLIB}/site-packages"/pip-*.dist-info',
        f'cp -a {ENTRY_DIR} "${{ROOT}}{ENTRY_DIR}"',
        f'mkdir "${{ROOT}}{OPENSSL_DIR}"',
        f': > "${{ROOT}}{OPENSSL_CONFIG}"',
    ]
    # The ELF files are listed, and the libraries they need taken from this stage, last.
    assert lines[empty + 1:] == ['bash /fixed-start/elf_needed.sh "${ROOT}" /fixed_start_elf.txt /']


def test_the_root_holds_no_pyinstaller_pip_or_static_interpreter_archive_and_no_package_of_the_base():
    (freeze,) = [s for s in _stages(_read(DOCKERFILE)) if re.match(r"FROM python:3\.14-alpine AS pyfreeze\n", s)]
    pip_lines = re.findall(r"^RUN pip install (?:[^\n]*\\\n)*[^\n]*$", freeze, re.M)
    assert len(pip_lines) == 2, "the securelock's packages, then the dApp's requirements"
    packages = [word.strip('"') for word in pip_lines[0].split("pip install", 1)[1].replace("\\", " ").split()
                if not word.startswith("-")]
    assert not [p for p in packages if re.match(r"(pyinstaller|setuptools|wheel|pip)\b", p, re.I)], packages
    for script in (PYFREEZE, ENCLAVE_ROOT_SH, BINARY_FS_BUILD):
        assert "pyinstaller" not in "\n".join(_commands(_read(script))).lower(), script.name
    lines = _commands(_read(ENCLAVE_ROOT_SH))
    # The base's site-packages (its pip, setuptools, wheel and packaging) go
    # before this stage's are copied, pip after, and the archive is removed.
    base = lines.index('rm -rf "${ROOT}${STDLIB}/site-packages"')
    own = lines.index('cp -a "${STDLIB}/site-packages" "${ROOT}${STDLIB}/site-packages"')
    pip = lines.index('rm -rf "${ROOT}${STDLIB}/site-packages/pip" "${ROOT}${STDLIB}/site-packages"/pip-*.dist-info')
    assert lines.index('cp -a /scone-python/lib/python3.14 "${ROOT}${STDLIB}"') < base < own < pip
    assert 'find "${ROOT}${STDLIB}" -name libpython3.14.a -exec rm {} \\;' in lines


@pytest.mark.parametrize("network,image", [
    ("BLOXBERG_TESTNET_UNSAFE", "etny-pynithy-testnet-unsafe"),
    ("BLOXBERG_TESTNET", "etny-pynithy-testnet"),
    ("POLYGON_AMOY", "ecld-pynithy-amoy"),
])
@pytest.mark.parametrize("production", [False, True])
def test_the_rendered_dockerfile_links_the_launcher_with_the_session_s_name_and_leaves_no_token(network, image,
                                                                                               production):
    dockerfile = build.render_securelock_dockerfile(_read(DOCKERFILE), network, SESSION_NAME, image, "1G", NO_ESR,
                                                    production)
    lines = dockerfile.split("\n")
    assert [line for line in lines if re.search(r"__[A-Z][A-Z0-9_]*__", line)] == []
    assert _link(SESSION_NAME) in lines
    assert [line for line in lines if line.startswith("RUN scone-signer sign ")] == [
        "RUN scone-signer sign --key=/enclave-key.pem --env --heap=1G --stack=4M --tcs=64 --dlopen=1 "
        f"--extensions=/lib/libbinary-fs.so{' --production' if production else ''} /usr/local/bin/python"]
    assert "ENV SCONE_ALLOW_DLOPEN=1" in lines
    assert f"ENV BUCKET_NAME={image}-v3" in lines
    assert f"ENV SECURELOCK_SESSION={SESSION_NAME}" in lines


@pytest.mark.parametrize("session", ["a b", 'a"b', "a'b", "a\\b", "a$b", "a/b", "a\nb", ""])
def test_a_session_name_that_is_not_letters_digits_dots_dashes_and_underscores_is_not_rendered(session):
    with pytest.raises(ValueError, match="the session name"):
        build.render_securelock_dockerfile(_read(DOCKERFILE), "BLOXBERG_TESTNET_UNSAFE", session,
                                           "etny-pynithy-testnet-unsafe", "1G", NO_ESR, False)


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


def test_the_build_directory_holds_the_fixed_start_and_the_three_build_scripts(tmp_path):
    assert build.copy_from_module_to_build_dir(tmp_path) is True
    copied = tmp_path / "securelock" / "fixed-start"
    assert sorted(p.name for p in copied.iterdir()) == sorted([*SHARED, "securelock.h"])
    assert sorted(p.name for p in FIXED_START.iterdir()) == sorted([*SHARED, "securelock.h"])
    for name in (*SHARED, "securelock.h"):
        assert (copied / name).read_bytes() == (FIXED_START / name).read_bytes()
    scripts = tmp_path / "securelock" / "scripts"
    assert sorted(p.name for p in scripts.iterdir()) == ["binary-fs-build.sh", "enclave-root.sh", "pyfreeze.sh"]
    for path in scripts.iterdir():
        assert path.read_bytes() == (SECURELOCK / "scripts" / path.name).read_bytes()


def test_fixed_start_c_tree_digest_sh_and_elf_needed_sh_are_byte_identical_to_etny_nodenithys_when_that_checkout_is_present():
    if not (NODENITHY_FIXED_START / "fixed_start.c").exists():
        pytest.skip(f"no etny-nodenithy checkout at {NODENITHY} (set ETNY_NODENITHY)")
    for name in SHARED:
        assert _read(FIXED_START / name) == _read(NODENITHY_FIXED_START / name), name


COMPILE_LINE = re.compile(r"^python -m compileall (.*) /usr/local/lib/python3\.14/site-packages \\$")


def _compileall_arguments():
    """The arguments pyfreeze.sh gives compileall."""
    (line,) = [m.group(1) for m in map(COMPILE_LINE.match, (x.strip() for x in _read(PYFREEZE).split("\n"))) if m]
    return line.split()


def test_the_freeze_stage_compiles_site_packages_with_unchecked_hash_headers_after_it_renders_the_entry():
    lines = [line.strip() for line in _read(PYFREEZE).split("\n")]
    render = lines.index("mv securelock.py.tmp securelock.py")
    remove = lines.index("find /usr/local/lib/python3.14/site-packages /etny-securelock -depth -type d "
                         "-name __pycache__ -exec rm -rf {} \\;")
    (compile_,) = [i for i, line in enumerate(lines) if COMPILE_LINE.match(line)]
    assert render < remove < compile_
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


# A musl C library (the one SCONE's runtime is built on), gcc, bash for
# tree_digest.sh, and the right to chroot: the launcher reads the file system
# from /, so a start is made inside a root of the test's own.
def _toolchain_missing():
    if sys.platform != "linux":
        return f"the host is {sys.platform}, not Linux"
    if not any(re.match(r"ld-musl-.*\.so\.1$", f) for f in os.listdir("/lib")):
        return "the host C library is not musl"
    if shutil.which("gcc") is None:
        return "gcc is not on PATH"
    if shutil.which("bash") is None:
        return "bash is not on PATH"
    if os.geteuid() != 0:
        return "the tests chroot, and the user is not root"
    return None


def _root_digest(root):
    """The digest of the root at `root` as fixed_start.c defines it, computed
    here from the definition."""
    h = hashlib.sha256()

    def record(*fields):
        for field in fields:
            h.update(os.fsencode(field) + b"\0")

    def entries(directory, at):
        for name in sorted(os.listdir(directory), key=os.fsencode):
            x, p = f"{directory}/{name}", f"{at}/{name}"
            st = os.lstat(x)
            if stat.S_ISREG(st.st_mode):
                data = Path(x).read_bytes()
                record("F" + p, str(len(data)), hashlib.sha256(data).hexdigest())
            elif stat.S_ISLNK(st.st_mode):
                record("L" + p, os.readlink(x))
            elif stat.S_ISDIR(st.st_mode):
                record("D" + p)
                entries(x, p)
            else:
                record("O" + p)

    entries(str(root), "")
    return h.hexdigest()


def _make_image(root, reverse=False, mode=0o644, mtime=1_000_000_000):
    """The files of an image's root for the launcher's tests under `root`: the
    entry's directory with the entry, a sibling module, the dApp's backend and
    the empty OpenSSL configuration, and the standard library's with a module
    and a package, a json package, names whose bytewise order differs from a
    sort of the whole paths (pkg/, pkg-b.py, pkg.py), a .pth file, bytecode
    and a symbolic link. reverse reverses the order the entries are made in;
    mode and mtime set the files' modes and times."""
    files = [(f"{ENTRY_DIR}/securelock.py", "import etny_crypto\n"), (f"{ENTRY_DIR}/etny_crypto.py", "X = 1\n"),
             (f"{ENTRY_DIR}/serverless/backend.py", "def hello(m):\n    return m\n"), (OPENSSL_CONFIG, ""),
             (f"{STDLIB}/pkg/__init__.py", "X = 1\n"), (f"{STDLIB}/pkg-b.py", "B = 1\n"),
             (f"{STDLIB}/pkg.py", "import pkg\n"), (f"{STDLIB}/json/__init__.py", "dumps = repr\n"),
             (f"{STDLIB}/site-packages/dist.pth", "./pkg\n"),
             (f"{STDLIB}/site-packages/mod/__pycache__/m.cpython-314.pyc", "\0\1bytecode")]
    steps = []
    for rel, text in files:
        def write(rel=rel, text=text):
            path = Path(f"{root}{rel}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            os.chmod(path, mode)
            os.utime(path, (mtime, mtime))
        steps.append(write)

    def link():
        Path(f"{root}{STDLIB}").mkdir(parents=True, exist_ok=True)
        os.symlink("pkg.py", f"{root}{STDLIB}/link.py")
    steps.append(link)
    Path(root).mkdir(parents=True, exist_ok=True)
    for step in (reversed(steps) if reverse else steps):
        step()


# The lines the listing holds for _make_image's files, the files without
# their sizes and hashes.
IMAGE_LISTING = [
    f"D {ENTRY_DIR}",
    f"F {ENTRY_DIR}/etny_crypto.py",
    f"D {OPENSSL_DIR}",
    f"F {OPENSSL_CONFIG}",
    f"F {ENTRY_DIR}/securelock.py",
    f"D {ENTRY_DIR}/serverless",
    f"F {ENTRY_DIR}/serverless/backend.py",
    "D /lib",
    "F /lib/{libc}",
    "D /usr", "D /usr/local", "D /usr/local/bin",
    f"F {VECTOR[0]}",
    "D /usr/local/lib",
    f"D {STDLIB}",
    f"D {STDLIB}/json",
    f"F {STDLIB}/json/__init__.py",
    f"L {STDLIB}/link.py -> pkg.py",
    f"D {STDLIB}/pkg",
    f"F {STDLIB}/pkg/__init__.py",
    f"F {STDLIB}/pkg-b.py",
    f"F {STDLIB}/pkg.py",
    f"D {STDLIB}/site-packages",
    f"F {STDLIB}/site-packages/dist.pth",
    f"D {STDLIB}/site-packages/mod",
    f"D {STDLIB}/site-packages/mod/__pycache__",
    f"F {STDLIB}/site-packages/mod/__pycache__/m.cpython-314.pyc",
]


def _listed(listing):
    """A listing's lines without the sizes and hashes of its files."""
    return [("F " + line.split(" ", 3)[3]) if line.startswith("F ") else line for line in listing.split("\n") if line]


STARTED_C = r"""#include <stdio.h>
#include <string.h>
#include <unistd.h>
extern char **environ;
int main(int argc, char **argv, char **envp)
{
    int i;
    int contiguous = 1;
    char **e;
    char cwd[4096];
    for (i = 0; i < argc; i++) {
        printf("arg %s\n", argv[i]);
        if (i > 0 && argv[i] != argv[i - 1] + strlen(argv[i - 1]) + 1)
            contiguous = 0;
    }
    for (e = environ; *e != NULL; e++)
        printf("env %s\n", *e);
    printf("envp %s\n", envp == environ ? "environ" : "other");
    printf("contiguous %d\n", contiguous);
    printf("cwd %s\n", getcwd(cwd, sizeof cwd) != NULL ? cwd : "?");
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

SESSION = "fs-test-session"


def _gcc(*args):
    r = subprocess.run(["gcc", *map(str, args)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _digests(root, out):
    """tree_digest.sh over a root: its header and its listing."""
    r = subprocess.run(["bash", str(TREE_DIGEST), str(root), f"{out}.h", f"{out}.txt"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return _read(f"{out}.h"), _read(f"{out}.txt")


def _compile_launcher(out, tree, over=None):
    """The launcher, compiled with the securelock's header, the digest header
    given and, after them, the header `over` when one is given."""
    _gcc("-shared", "-fPIC", "-O2", "-Wall", "-Wextra", "-Werror", f'-DFIXED_START_SESSION="{SESSION}"',
         "-include", HEADER, "-include", tree, *(["-include", over] if over else []), "-o", out, LAUNCHER)


class Build:
    """The root of the launcher's tests and what was compiled for it."""

    def __init__(self, d):
        self.dir = d
        self.root = d / "root"
        self.loader = d / "loader"
        self.launcher = self.root / "dev" / "libfixedstart.so"
        self.libc = next(f for f in os.listdir("/lib") if re.match(r"ld-musl-.*\.so\.1$", f))
        self.digest = None

    def under(self, p):
        return Path(f"{self.root}{p}")

    def put(self, p, content="x\n"):
        self.under(p).parent.mkdir(parents=True, exist_ok=True)
        self.under(p).write_text(content)

    def gone(self, p):
        path = self.under(p)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()

    def start(self, args=(), env=None, cwd="/"):
        """A start of the program inside the root, from the directory `cwd` of it."""
        def enter():
            os.chroot(self.root)
            os.chdir(cwd)
        return subprocess.run([VECTOR[0], *args], capture_output=True, text=True,
                              env={"ETNY_ORDER_ID": "7"} if env is None else env, preexec_fn=enter)

    def runs(self, why):
        r = self.start()
        assert r.returncode == 0, f"{why}: {r.stderr}"
        assert _lines(r.stdout, "arg") == VECTOR, why

    def refused_at(self, where, change, undo):
        """A start after change() is refused over the entry `where`; once
        undone, the next start runs the program's main."""
        change()
        try:
            _refusal(self.start(), f"the file tree is not the image's at {where}")
        finally:
            undo()
        self.runs(f"after the change at {where} is undone")

    def added_is_refused(self, p, where, top=None):
        """A start with the entry `p` added (a file, or a directory when it
        ends in /) is refused over `where`."""
        def add():
            if p.endswith("/"):
                self.under(p).mkdir(parents=True)
            else:
                self.put(p)
        self.refused_at(where, add, lambda: self.gone(top or p.rstrip("/")))


def _lines(out, tag):
    return [line[len(tag) + 1:] for line in out.split("\n") if line.startswith(f"{tag} ")]


def _refusal(r, text):
    assert r.returncode == REFUSED, r.stdout + r.stderr
    assert r.stdout == ""
    assert r.stderr == f"fixed start: the start is refused: {text}\n"


@pytest.fixture(scope="module")
def launcher(tmp_path_factory):
    missing = _toolchain_missing()
    if missing:
        pytest.skip(missing)
    b = Build(tmp_path_factory.mktemp("fixed-start"))
    d = b.dir
    (d / "started.c").write_text(STARTED_C)
    (d / "loader.c").write_text(LOADER_C)
    _gcc("-O2", "-o", b.loader, d / "loader.c")
    # The image's root: the entry's directory, the standard library's, the
    # program at the interpreter's path and the C library's loader the
    # program names. The program is linked to a launcher by its name and takes
    # it from /dev, which the launcher does not read: in an enclave the
    # extension is outside the file system the launcher sees.
    _make_image(b.root)
    (d / "none.h").write_text('#define FIXED_START_ROOT_DIGEST "' + "0" * 64 + '"\n')
    _compile_launcher(d / "libfixedstart.so", d / "none.h")
    b.under(VECTOR[0]).parent.mkdir(parents=True, exist_ok=True)
    _gcc("-O2", "-o", b.under(VECTOR[0]), d / "started.c", f"-L{d}", "-lfixedstart", "-Wl,-rpath,/dev")
    b.under("/lib").mkdir()
    shutil.copyfile(f"/lib/{b.libc}", b.under(f"/lib/{b.libc}"))
    os.chmod(b.under(f"/lib/{b.libc}"), 0o755)
    b.digest = _digests(b.root, d / "fixed_start_tree")
    # What a start finds beside the image's files.
    b.under("/dev").mkdir()
    b.put("/proc/1/cmdline", "python\0")
    b.put("/etc/hosts", "127.0.0.1 localhost\n")
    b.put("/etc/resolv.conf", "nameserver 127.0.0.11\n")
    _compile_launcher(b.launcher, d / "fixed_start_tree.h")
    return b


def test_a_program_started_through_the_launcher_runs_the_fixed_vector_with_the_fixed_variables_and_the_listed_keys(launcher):
    r = launcher.start(["-c", "import os", "-m", "http.server"],
                       {"PYTHONPATH": "/etc", "PYTHONSTARTUP": "/etc/hosts", "ETNY_ORDER_ID": "7", "ETNY_ORDER": "8",
                        "etny_order_id": "9", "ETNY_MODE": "PUBLIC_KEY", "HOME": "/root", "OPENSSL_CONF": "/etc/hosts",
                        "OPENSSL_MODULES": "/tmp", "ESR_CONTRACT_ADDRESS": "0x0Ea1728EAE108FD3B9340ae91451348E2Cc6b4E4",
                        "ESR_IDENTITY_SECRET": "secret", "ECAS_CAS_QUOTE": "PYTHONPATH=/etc"})
    assert r.returncode == 0, r.stderr
    assert _lines(r.stdout, "arg") == VECTOR
    assert _lines(r.stdout, "env") == FIXED_ENV + ["ETNY_ORDER_ID=7", "ECAS_CAS_QUOTE=PYTHONPATH=/etc",
                                                   "ESR_CONTRACT_ADDRESS=0x0Ea1728EAE108FD3B9340ae91451348E2Cc6b4E4"]
    assert _lines(r.stdout, "envp") == ["environ"]
    assert _lines(r.stdout, "contiguous") == ["1"]


def test_every_listed_key_is_passed_with_its_value_as_it_is_up_to_the_launchers_space(launcher):
    env = {key: f"value {i} = -c x" for i, key in enumerate(ALLOWED)}
    r = launcher.start(env=env)
    assert r.returncode == 0, r.stderr
    assert _lines(r.stdout, "env") == FIXED_ENV + [f"{key}=value {i} = -c x" for i, key in enumerate(ALLOWED)]
    fits = "A" * (SPACE - sum(len(entry) + 1 for entry in FIXED_ENV) - len("ECAS_CAS_QUOTE=") - 1)
    assert launcher.start(env={"ECAS_CAS_QUOTE": fits}).returncode == 0
    _refusal(launcher.start(env={"ECAS_CAS_QUOTE": fits + "A"}), "the environment exceeds the launcher's space")


def test_a_start_that_did_not_come_through_the_launcher_is_refused_when_its_object_is_loaded(launcher):
    r = subprocess.run([launcher.loader, launcher.launcher], capture_output=True, text=True)
    _refusal(r, "the start did not come through the launcher")


def test_the_program_runs_in_the_fixed_working_directory_whatever_directory_the_start_is_made_from(launcher):
    for cwd in ("/", ENTRY_DIR, STDLIB, "/proc/1"):
        r = launcher.start(cwd=cwd)
        assert r.returncode == 0, r.stderr
        assert _lines(r.stdout, "cwd") == [WORKING_DIRECTORY], cwd


def test_a_working_directory_that_cannot_be_entered_is_refused(launcher):
    (launcher.dir / "pwd.h").write_text(f'#undef FIXED_START_PWD\n#define FIXED_START_PWD "{ENTRY_DIR}/none"\n')
    kept = launcher.launcher.with_suffix(".kept")
    launcher.launcher.rename(kept)
    try:
        _compile_launcher(launcher.launcher, launcher.dir / "fixed_start_tree.h", launcher.dir / "pwd.h")
        _refusal(launcher.start(), f"the working directory cannot be entered: {ENTRY_DIR}/none")
    finally:
        kept.rename(launcher.launcher)
    launcher.runs("with the launcher of the securelock's header again")


def test_the_generator_writes_the_digest_of_the_root_as_fixed_start_c_defines_it_and_lists_the_entries_it_covers(launcher):
    image = launcher.dir / "image"
    _make_image(image)
    Path(f"{image}{VECTOR[0]}").parent.mkdir(parents=True)
    shutil.copyfile(launcher.under(VECTOR[0]), f"{image}{VECTOR[0]}")
    (image / "lib").mkdir()
    shutil.copyfile(launcher.under(f"/lib/{launcher.libc}"), image / "lib" / launcher.libc)
    header, listing = _digests(image, launcher.dir / "check")
    d = _root_digest(image)
    assert header == ("/* Written by tree_digest.sh: the digest of the root the binary-fs is made of. */\n"
                      f'#define FIXED_START_ROOT_DIGEST "{d}"\n')
    assert _listed(listing) == [line.format(libc=launcher.libc) for line in IMAGE_LISTING] + [f"= / {d}"]
    assert f"F 0 {hashlib.sha256(b'').hexdigest()} {OPENSSL_CONFIG}" in listing.split("\n")
    # The same root as the one the launcher of these tests was compiled for.
    assert (header, listing) == launcher.digest


def test_a_start_whose_root_is_the_one_the_digest_was_written_for_runs_the_program_s_main(launcher):
    launcher.runs("the root as it was made")


def test_a_changed_file_is_refused_the_entry_the_dapp_s_backend_a_standard_library_module_and_bytecode(launcher):
    for p in (f"{ENTRY_DIR}/securelock.py", f"{ENTRY_DIR}/serverless/backend.py", f"{STDLIB}/json/__init__.py",
              f"{STDLIB}/site-packages/mod/__pycache__/m.cpython-314.pyc", OPENSSL_CONFIG):
        path = launcher.under(p)
        was = path.read_bytes()
        launcher.refused_at("/", lambda: path.write_text("import os\n"), lambda: path.write_bytes(was))
        launcher.refused_at("/", lambda: path.write_bytes(was + b" "), lambda: path.write_bytes(was))


def test_an_added_file_is_refused_anywhere_under_the_root(launcher):
    # Where the interpreter and site look for code: the entry's directory,
    # the dApp's package, the standard library and site-packages.
    launcher.added_is_refused(f"{ENTRY_DIR}/added.py", "/")
    launcher.added_is_refused(f"{ENTRY_DIR}/serverless/added.py", "/")
    launcher.added_is_refused(f"{ENTRY_DIR}/cryptography/__init__.py", "/", f"{ENTRY_DIR}/cryptography")
    launcher.added_is_refused(f"{STDLIB}/sitecustomize.py", "/")
    launcher.added_is_refused(f"{STDLIB}/json.py", "/")
    launcher.added_is_refused(f"{STDLIB}/site-packages/sitecustomize.py", "/")
    launcher.added_is_refused(f"{STDLIB}/site-packages/added.pth", "/")
    launcher.added_is_refused(f"{STDLIB}/lib-dynload/_ssl.cpython-314-x86_64-linux-musl.so", "/", f"{STDLIB}/lib-dynload")
    # What the path configuration reads by name beside the interpreter, and
    # the places it takes for a build tree or another installation.
    launcher.added_is_refused("/usr/local/lib/python314.zip", "/")
    launcher.added_is_refused("/usr/local/bin/python._pth", "/")
    launcher.added_is_refused("/usr/local/bin/pyvenv.cfg", "/")
    launcher.added_is_refused("/usr/local/pyvenv.cfg", "/")
    launcher.added_is_refused("/usr/local/bin/pybuilddir.txt", "/")
    launcher.added_is_refused("/usr/local/bin/python3", "/")
    launcher.added_is_refused("/usr/local/bin/Modules/Setup.local", "/", "/usr/local/bin/Modules")
    launcher.added_is_refused("/usr/local/bin/Lib/os.py", "/", "/usr/local/bin/Lib")
    launcher.added_is_refused("/usr/local/bin/lib/python3.14/os.py", "/", "/usr/local/bin/lib")
    launcher.added_is_refused("/usr/local/Lib/os.py", "/", "/usr/local/Lib")
    launcher.added_is_refused("/usr/Lib/os.py", "/", "/usr/Lib")
    launcher.added_is_refused("/usr/lib/python3.14/os.py", "/", "/usr/lib")
    launcher.added_is_refused("/usr/lib/python314.zip", "/", "/usr/lib")
    # Where the loader and OpenSSL look, and the fixed HOME.
    launcher.added_is_refused("/usr/lib/libssl.so.3", "/", "/usr/lib")
    launcher.added_is_refused("/usr/local/lib/libadded.so", "/")
    launcher.added_is_refused("/lib/libadded.so", "/")
    launcher.added_is_refused(f"{OPENSSL_DIR}/legacy.so", "/")
    launcher.added_is_refused("/usr/local/ssl/openssl.cnf", "/", "/usr/local/ssl")
    launcher.added_is_refused("/tmp/.netrc", "/", "/tmp")
    launcher.added_is_refused("/added.py", "/")


def test_an_added_directory_without_an_entry_is_refused(launcher):
    launcher.added_is_refused("/tmp/", "/")
    launcher.added_is_refused(f"{STDLIB}/site-packages/namespace/", "/")
    launcher.added_is_refused(f"{ENTRY_DIR}/serverless/namespace/", "/")
    launcher.added_is_refused("/usr/lib/", "/")


def test_a_removed_file_a_retargeted_link_a_file_in_place_of_a_link_and_a_link_in_place_of_a_file_are_refused(launcher):
    file = launcher.under(f"{STDLIB}/pkg-b.py")
    link = launcher.under(f"{STDLIB}/link.py")

    def relink(target):
        link.unlink()
        os.symlink(target, link)

    def file_for_link():
        link.unlink()
        link.write_text("import pkg\n")

    def link_for_file():
        file.unlink()
        os.symlink("pkg.py", file)

    def file_again():
        file.unlink()
        file.write_text("B = 1\n")

    launcher.refused_at("/", file.unlink, lambda: file.write_text("B = 1\n"))
    launcher.refused_at("/", lambda: relink("pkg-b.py"), lambda: relink("pkg.py"))
    launcher.refused_at("/", file_for_link, lambda: relink("pkg.py"))
    launcher.refused_at("/", link_for_file, file_again)


def test_dev_and_proc_are_not_read_whatever_they_hold_the_start_runs(launcher):
    launcher.put("/dev/added/file")
    launcher.put("/proc/" + "d/" * (DEPTH_MAX + 6) + "file")
    os.symlink("/etc/hosts", launcher.under("/dev/link"))
    try:
        launcher.runs("with files, a link and a deep directory below /dev and /proc")
    finally:
        launcher.gone("/dev/added")
        launcher.gone("/dev/link")
        launcher.gone("/proc/d")
    kept = launcher.dir / "proc.kept"
    launcher.under("/proc").rename(kept)
    try:
        launcher.runs("without /proc")
        launcher.put("/proc")
        launcher.runs("with a file at /proc")
        launcher.gone("/proc")
    finally:
        kept.rename(launcher.under("/proc"))


def test_etc_holds_hosts_and_resolv_conf_either_neither_or_is_not_there_and_nothing_else(launcher):
    launcher.runs("with both files")
    launcher.under("/etc/hosts").rename(launcher.under("/dev/hosts.kept"))
    try:
        launcher.runs("with resolv.conf alone")
        launcher.under("/etc/resolv.conf").rename(launcher.under("/dev/resolv.conf.kept"))
        try:
            launcher.runs("with an empty /etc")
            launcher.under("/etc").rmdir()
            launcher.runs("without /etc")
            launcher.refused_at("/etc", lambda: launcher.put("/etc"), lambda: launcher.gone("/etc"))
            launcher.under("/etc").mkdir()
        finally:
            launcher.under("/dev/resolv.conf.kept").rename(launcher.under("/etc/resolv.conf"))
        # A name of the list that is a directory hides what it holds.
        launcher.refused_at("/etc/hosts", lambda: launcher.put("/etc/hosts/added"), lambda: launcher.gone("/etc/hosts"))
        os.symlink("/proc/1/cmdline", launcher.under("/etc/hosts"))
        launcher.runs("with a link at /etc/hosts")
        launcher.gone("/etc/hosts")
    finally:
        launcher.under("/dev/hosts.kept").rename(launcher.under("/etc/hosts"))
    launcher.added_is_refused("/etc/passwd", "/etc/passwd")
    launcher.added_is_refused("/etc/ld-musl-x86_64.path", "/etc/ld-musl-x86_64.path")
    launcher.added_is_refused("/etc/localtime", "/etc/localtime")
    launcher.added_is_refused("/etc/ssl/openssl.cnf", "/etc/ssl", "/etc/ssl")
    launcher.added_is_refused("/etc/ssl/cert.pem", "/etc/ssl", "/etc/ssl")
    launcher.added_is_refused("/etc/hosts.d/", "/etc/hosts.d")
    launcher.added_is_refused("/etc/Hosts", "/etc/Hosts")


def test_the_session_s_directories_hold_its_certificates_and_its_key_by_their_exact_names_and_nothing_else(launcher):
    try:
        for p in (f"/app/{SESSION}/ca.pem", f"/app/{SESSION}/cert.pem", f"/private/{SESSION}/key.pem"):
            launcher.put(p, "-----BEGIN-----\n")
            launcher.runs(f"with the session's files up to {p}")
        launcher.added_is_refused(f"/app/{SESSION}/enclave_pub_cert.pem", f"/app/{SESSION}/enclave_pub_cert.pem")
        launcher.added_is_refused(f"/app/{SESSION}/key.pem", f"/app/{SESSION}/key.pem")
        launcher.added_is_refused(f"/private/{SESSION}/cert.pem", f"/private/{SESSION}/cert.pem")
        launcher.added_is_refused("/app/.env", "/app/.env")
        launcher.added_is_refused("/app/result.txt", "/app/result.txt")
        launcher.added_is_refused("/app/other-session/cert.pem", "/app/other-session", "/app/other-session")
        launcher.added_is_refused("/private/key.pem", "/private/key.pem")
        launcher.added_is_refused(f"/private/{SESSION}x/key.pem", f"/private/{SESSION}x", f"/private/{SESSION}x")
        # A listed name that is a directory, and a directory's name that is a file.
        launcher.gone(f"/app/{SESSION}/cert.pem")
        launcher.refused_at(f"/app/{SESSION}/cert.pem", lambda: launcher.put(f"/app/{SESSION}/cert.pem/added"),
                            lambda: launcher.gone(f"/app/{SESSION}/cert.pem"))
        launcher.gone("/private")
        launcher.refused_at(f"/private/{SESSION}", lambda: launcher.put(f"/private/{SESSION}"),
                            lambda: launcher.gone("/private"))
        launcher.refused_at("/private", lambda: launcher.put("/private"), lambda: launcher.gone("/private"))
    finally:
        launcher.gone("/app")
        launcher.gone("/private")
    launcher.runs("without the session's directories")


def test_an_entry_deeper_than_the_launcher_takes_is_refused_in_the_image_s_tree_and_below_it(launcher):
    def chain(n):
        return "/" + "/".join(["d"] * n)

    # A directory at depth DEPTH_MAX is read and found not to be the image's; an entry below it is too deep.
    launcher.refused_at("/", lambda: launcher.under(chain(DEPTH_MAX)).mkdir(parents=True), lambda: launcher.gone("/d"))
    launcher.under(chain(DEPTH_MAX + 1)).mkdir(parents=True)
    try:
        _refusal(launcher.start(), f"a path of the tree is deeper than the launcher takes: {chain(DEPTH_MAX + 1)}")
        launcher.under(chain(DEPTH_MAX + 200)).mkdir(parents=True)
        _refusal(launcher.start(), f"a path of the tree is deeper than the launcher takes: {chain(DEPTH_MAX + 1)}")
    finally:
        launcher.gone("/d")
    launcher.runs("without the deep directories")


def test_the_digest_does_not_depend_on_modes_times_or_the_order_the_entries_were_made_in(launcher):
    d = launcher.dir
    image = [ENTRY_DIR, STDLIB]
    beside = ["/dev", "/proc", "/etc"]
    for i, p in enumerate(image):
        launcher.under(p).rename(d / f"image{i}.kept")
    try:
        _make_image(launcher.root, reverse=True, mode=0o600, mtime=2_000_000_000)
        # The generator reads the image's files alone.
        for p in beside:
            launcher.under(p).rename(d / f"{p[1:]}.kept")
        try:
            assert _digests(launcher.root, d / "remade") == launcher.digest
            assert _digests(launcher.root, d / "again") == launcher.digest
        finally:
            for p in beside:
                (d / f"{p[1:]}.kept").rename(launcher.under(p))
        launcher.runs("the image's directories made again in another order")
    finally:
        for i, p in enumerate(image):
            shutil.rmtree(launcher.under(p))
            (d / f"image{i}.kept").rename(launcher.under(p))
