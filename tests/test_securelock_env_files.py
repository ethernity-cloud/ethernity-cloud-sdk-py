"""No securelock source of this package loads a .env file of its file system,
and no .env file goes into the securelock image.

The per-order value reaches the securelock in the environment the fixed start
passes and in the .env object of its store bucket, which read_env_str parses
from the string get_file_content returns and admits key by key
(NODE_ENV_ALLOWLIST). env_file_loads finds, in a Python source, every use of
python-dotenv that reads a file (load_dotenv, find_dotenv, dotenv_values or
DotEnv on a path, any other name of the dotenv module) and every literal that
names a .env file other than as the key of a bucket read, which covers a file
the code opens by a .env name to read into os.environ. The sources checked are
the securelock's src/ and the scaffold's src/serverless, which ecld-init gives
every dApp and the build bakes beside the securelock. No securelock source
imports dotenv, so the image installs no python-dotenv: the pip lines of
Dockerfile.tpl and Dockerfile.base.tpl name none (a dApp that imports it lists
it in its src/serverless/requirements.txt, which the build installs). The
trees Dockerfile.tpl copies (src/, scripts/, fixed-start/) hold no file named
.env or .env.<suffix>, and the Dockerfiles and the build scripts name none on
a line that is not a comment. The tests read names and Python sources; no
test reads a .env file."""
import ast
import re
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parent.parent
SECURELOCK = SDK / "ethernity_cloud_sdk_py" / "commands" / "pynithy" / "build" / "securelock"
SRC = SECURELOCK / "src"
SCRIPTS = SECURELOCK / "scripts"
TEMPLATE = SRC / "securelock.py.tmpl"
SCAFFOLD = SDK / "ethernity_cloud_sdk_py" / "templates" / "src" / "serverless"
DOCKERFILES = [SECURELOCK / "Dockerfile.tpl", SECURELOCK / "Dockerfile.base.tpl"]
IMAGE_TREES = [SRC, SCRIPTS, SECURELOCK / "fixed-start"]

# A file name of a .env file: .env, or .env.<suffix>.
ENV_FILE_NAME = re.compile(r"\.env(\..*)?")
# A literal that names a .env file: .env or .env.<suffix>, alone or as the
# last component of a path.
ENV_PATH = re.compile(r"(.*/)?\.env(\.[\w.-]+)?")
# A .env file named as a word of a shell or Dockerfile line.
ENV_PATH_WORD = re.compile(r"(^|[\s'\"=:/(<>])\.env(\.[\w.-]+)?(?=$|[\s'\";|&)<>])")
# The calls that read a store bucket's object by its key: a .env literal there
# is the object's key, not a path of the file system.
BUCKET_READS = frozenset({"get_file_content", "get_file_content_bytes", "get_env_content", "is_object_in_bucket"})
# python-dotenv's readers of a file whatever their arguments.
FILE_READERS = frozenset({"load_dotenv", "find_dotenv"})
# python-dotenv's parsers, which read a file unless given a stream alone.
PARSERS = frozenset({"dotenv_values", "DotEnv"})
PARSE_KEYWORDS = frozenset({"stream", "verbose", "interpolate", "encoding"})


def _called(node):
    """The name a reference ends with: f for f, x.f and x.y.f."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_dotenv(module):
    return module == "dotenv" or module.startswith("dotenv.")


def env_file_loads(source):
    """The places of the Python `source` that load a .env file of the file
    system or may, as "<line>: <what>", in the order of the lines."""
    tree = ast.parse(source)
    found = []
    docstrings = set()
    bucket_keys = set()
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
        elif isinstance(node, ast.Call) and _called(node.func) in BUCKET_READS:
            bucket_keys.update(id(arg) for arg in node.args)
        elif isinstance(node, ast.Import):
            modules.update((alias.asname or alias.name.split(".")[0]) for alias in node.names if _is_dotenv(alias.name))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and _is_dotenv(node.module):
            found += [(node.lineno, f"from {node.module} import {alias.name}") for alias in node.names
                      if alias.name not in PARSERS]
        elif isinstance(node, (ast.Name, ast.Attribute)) and _called(node) in FILE_READERS:
            found.append((node.lineno, _called(node)))
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules
              and node.attr not in PARSERS):
            found.append((node.lineno, f"{node.value.id}.{node.attr}"))
        elif isinstance(node, ast.Call) and _called(node.func) in PARSERS:
            keywords = {keyword.arg for keyword in node.keywords}
            if node.args or "stream" not in keywords or not keywords <= PARSE_KEYWORDS:
                found.append((node.lineno, f"{_called(node.func)} on a path"))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes))
              and id(node) not in docstrings and id(node) not in bucket_keys):
            text = node.value.decode("latin-1") if isinstance(node.value, bytes) else node.value
            if ENV_PATH.fullmatch(text):
                found.append((node.lineno, f"the .env file name {text!r}"))
    return [f"{line}: {what}" for line, what in sorted(found)]


def _sources():
    """Every Python file of the securelock's src/ and of the scaffold's
    src/serverless, __pycache__ left out."""
    return sorted(path for root in (SRC, SCAFFOLD) for path in root.rglob("*")
                  if path.is_file() and "__pycache__" not in path.parts
                  and (path.suffix == ".py" or path.name.endswith(".py.tmpl")))


def _relative(path):
    return path.relative_to(SDK).as_posix()


def _env_file_lines(text):
    """The lines of a shell script or Dockerfile `text` that are not comment
    lines and name a .env file, as "<line>: <text>"."""
    return [f"{number}: {line.strip()}" for number, line in enumerate(text.splitlines(), 1)
            if not line.strip().startswith("#") and ENV_PATH_WORD.search(line.strip())]


def _pip_lines(text):
    """The `RUN pip install` lines of a Dockerfile, with their continuations."""
    return re.findall(r"^RUN pip install (?:[^\n]*\\\n)*[^\n]*$", text, re.M)


def test_no_securelock_source_loads_a_dotenv_file():
    sources = _sources()
    assert TEMPLATE in sources and SCAFFOLD / "backend.py" in sources
    loads = {_relative(path): env_file_loads(path.read_text(encoding="utf-8")) for path in sources}
    assert {path: found for path, found in loads.items() if found} == {}


@pytest.mark.parametrize("source", [
    "from dotenv import load_dotenv\nload_dotenv()\n",
    "import dotenv\ndotenv.load_dotenv('/app/.env')\n",
    "from dotenv import find_dotenv, load_dotenv\nload_dotenv(find_dotenv())\n",
    "from dotenv import dotenv_values\nconfig = dotenv_values('/app/.env')\n",
    "from dotenv import dotenv_values\nconfig = dotenv_values(dotenv_path=path)\n",
    "from dotenv import dotenv_values\nos.environ.update(dotenv_values())\n",
    "import dotenv\nvalue = dotenv.get_key(path, 'ETNY_ORDER_ID')\n",
    "from dotenv.main import DotEnv\nvalues = DotEnv(path).dict()\n",
    "import dotenv as d\nd.load_dotenv(override=True)\n",
    "with open('.env') as f:\n    for line in f:\n        key, value = line.split('=', 1)\n        os.environ[key] = value\n",
    "with open('/app/.env', 'rb') as f:\n    os.environ.update(parse(f.read()))\n",
    "os.environ.update(parse(Path('/app/.env').read_text()))\n",
    "path = os.path.join(os.getcwd(), '.env')\nos.environ.update(parse(open(path).read()))\n",
    "os.environ.update(parse(open(f'{root}/.env').read()))\n",
    "os.environ.update(parse((Path(root) / '.env.local').read_text()))\n",
    "os.environ.update(parse(open(b'./app/.env').read()))\n",
])
def test_the_check_finds_each_form_of_a_dotenv_file_load(source):
    assert env_file_loads(source)


def _read_env_str_source():
    """read_env_str of the securelock's template, the parse of the bucket's
    .env that the enclave runs."""
    text = TEMPLATE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == "read_env_str")
    return "import io\nimport os\nimport re\n" + "\n".join(
        line[4:] for line in ast.get_source_segment(text, function, padded=True).splitlines())


@pytest.mark.parametrize("source", [
    _read_env_str_source(),
    "status, env_content = swiftStreamClient.get_file_content('__BUCKET_NAME__', '.env')\n",
    "import io\nfrom dotenv import dotenv_values\nvalues = dotenv_values(stream=io.StringIO(env_content))\n",
    "logging.info('No .env in SwiftStream; public key emitted, exiting (cert-harvest mode)')\n",
    "logging.info(f'Ignoring non-allowlisted .env key from the node bucket: {key}')\n",
    "def read():\n    '''Reads nothing from /app/.env'''\n    # load_dotenv is not called\n    return os.environ.get('ETNY_ORDER_ID')\n",
    "NAME = '.environment'\nOTHER = 'production.env.d'\n",
])
def test_the_check_passes_a_parse_of_the_bucket_s_content_its_reads_and_text(source):
    assert env_file_loads(source) == []


def test_no_securelock_source_imports_dotenv():
    imports = {}
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                 for alias in node.names if _is_dotenv(alias.name)]
        names += [node.module for node in ast.walk(tree)
                  if isinstance(node, ast.ImportFrom) and node.module and _is_dotenv(node.module)]
        if names:
            imports[_relative(path)] = names
    assert imports == {}


def test_the_securelock_image_installs_no_python_dotenv():
    lines = {path.name: _pip_lines(path.read_text(encoding="utf-8")) for path in DOCKERFILES}
    assert lines["Dockerfile.tpl"], "the freeze stage installs the securelock's packages"
    for name, pip in lines.items():
        for line in pip:
            assert not re.search(r"python[-_.]?dotenv\b", line, re.I), f"{name}: {line}"


def test_no_tree_the_dockerfile_copies_holds_a_file_named_dotenv():
    assert all(tree.is_dir() for tree in IMAGE_TREES)
    named = [_relative(path) for tree in IMAGE_TREES for path in tree.rglob("*")
             if ENV_FILE_NAME.fullmatch(path.name)]
    assert named == []


def test_the_dockerfiles_and_the_build_scripts_name_no_dotenv_file():
    files = [*DOCKERFILES, *sorted(SCRIPTS.glob("*.sh"))]
    assert SCRIPTS / "binary-fs-build.sh" in files
    lines = {_relative(path): _env_file_lines(path.read_text(encoding="utf-8")) for path in files}
    assert {path: found for path, found in lines.items() if found} == {}


@pytest.mark.parametrize("line, names", [
    ("cat .env", True),
    ("COPY ./src/.env /etny-securelock/", True),
    ("RUN cat /etny-securelock/.env", True),
    ("cp '.env.local' /tmp/", True),
    ("# cat .env", False),
    ("COPY ./src /etny-securelock/", False),
    ('echo "${NODE_ENV}"', False),
    ('      "web3>=6" cryptography ecdsa pyasn1 tinyec minio pynacl', False),
])
def test_the_line_check_finds_a_dotenv_file_on_a_build_line(line, names):
    assert bool(_env_file_lines(line)) is names
