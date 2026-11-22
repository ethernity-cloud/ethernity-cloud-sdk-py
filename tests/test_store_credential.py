"""The store credential of the pynithy securelock: the reader
(src/store_credential.py), the template's use of it, the host path of the
binary-fs build, the image's label, the object names the securelock puts,
and what ecld-build and ecld-publish write of them. The node agent mounts
the credential of the securelock's store user read-only at
/run/ecld/store.json; an image labelled cloud.ethernity.securelock.store=file
reads it.

  - store_credential.py is the reader's text: its SHA-256 and its length,
    over its bytes with LF line endings.
  - The reader's answers: VECTORS (a file's content, the source and the
    keys or the reason), the files given as bytes (4 bytes of UTF-16 with a
    BOM, a credential followed by spaces up to 4096 and up to 4097 bytes,
    UTF-16LE text, 8 MiB of spaces read as one read of 4097 bytes, a UTF-8
    BOM), a directory at the path, nothing at the path, a regular file as
    the path's parent, and the call with no argument. Each answer comes with
    one log line, through a `log` of the test's own and through
    logging.info, which names the path and the reason and holds neither key
    and nothing of the file.
  - The literal pair is in no file of the securelock tree but the reader.
    securelock.py.tmpl imports the reader once, calls load() once, with no
    argument, in the statement before the one that constructs its one
    SwiftStreamService, passes that the two keys and names them nowhere
    else; no other module imports the reader.
  - scripts/binary-fs-build.sh declares the host paths /etc/resolv.conf,
    /run/ecld and /etc/hosts, in that order, as the last three lines of
    EXEC.
  - Dockerfile.tpl holds LABEL cloud.ethernity.securelock.store="file" in
    its last stage, on the line before its one ENTRYPOINT, and no other
    LABEL; Dockerfile.base.tpl holds none; no Dockerfile of the image holds
    a USER instruction (the credential file is readable by root alone).
  - PUT_PATTERNS are the object names the agent's policy lets a securelock's
    store user put. ADMITTED (every name a securelock puts) matches them and
    REFUSED (the node's, the client's and the trustedzone's objects) does
    not. Every name the securelock passes to a put matches one, and it asks
    no removal: over a plain task and a session run from the template
    (test_f_log_confidentiality's run), and over a plain task's commit, a
    session step's commit and a fence under esr_local. In the sources only
    swift_stream_service.py calls a removal or an upload of the store client.
  - ecld-build, run with its docker commands recorded: the build context
    holds the reader and the host path, the Dockerfile docker builds the
    securelock from ends with the label and the ENTRYPOINT, and the image is
    built, tagged and pushed by three commands that name no other label.
  - ecld-publish's two composes, for a network with a LAS and for an -unsafe
    one: no volume at /run/ecld (the mount is the agent's), no `user` on an
    enclave service, and the securelock named by the image the build pushed.

The store tests run the repository's SwiftStreamService, built from the
reader's answer, against a MinIO: they are skipped unless
ECLD_TEST_STORE_ENDPOINT names its host:port, with ECLD_TEST_STORE_BUCKET
(a bucket that holds `.env`), ECLD_TEST_STORE_ROLE (`trustedzone` or
`securelock`) and ECLD_TEST_STORE_CREDENTIAL (the path of the store.json of
a user the node agent made for that bucket and role). The bucket answers
its head; an absent object answers NoSuchKey; a trustedzone's user puts,
reads and removes any object; a securelock's user reads any object, puts
every name of ADMITTED, removes nothing and puts no name of REFUSED, in one
request or in parts; an object of 6 MiB is put in parts as result.txt;
every call on etny-bucket-integration is refused."""
import ast
import builtins
import hashlib
import importlib
import inspect
import io
import json
import logging
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from ethernity_cloud_sdk_py.commands.config import Config
from ethernity_cloud_sdk_py.commands.pynithy import build
from tests import test_f_log_confidentiality as order
from tests.f_session_support import BUCKET, CALLER, ORDER_ID, SRC, FakeApp, content, session_env

SECURELOCK = SRC.parent
TEMPLATE = SRC / "securelock.py.tmpl"
READER = SRC / "store_credential.py"
READER_SHA256 = "471fad84c57cded9db21e14aab7f3edabbc69ad5a269c53351916b9ce7974089"
READER_BYTES = 1905
CREDENTIAL_PATH = "/run/ecld/store.json"
LITERAL = "swiftstreamadmin"
LABEL = 'LABEL cloud.ethernity.securelock.store="file"'
MAX_BYTES = 4096

# content: the file's text, whose UTF-8 encoding is its bytes; source file:
# the two keys the reader returns; source literal: the reason it logs.
VECTORS = json.loads(r'''[
 {"id":"V01","content":"{\"accessKey\":\"ecldtz0123456789abcd\",\"secretKey\":\"0123456789abcdef0123456789abcdef01234567\"}\n","source":"file","accessKey":"ecldtz0123456789abcd","secretKey":"0123456789abcdef0123456789abcdef01234567"},
 {"id":"V02","content":"{\"accessKey\":\"swiftstreamadmin\",\"secretKey\":\"swiftstreamadmin\"}\n","source":"file","accessKey":"swiftstreamadmin","secretKey":"swiftstreamadmin"},
 {"id":"V03","content":"{\"accessKey\":\"abc\",\"secretKey\":\"12345678\"}","source":"file","accessKey":"abc","secretKey":"12345678"},
 {"id":"V04","content":" {\r\n\t\"accessKey\" : \"abc\" ,\r\n\t\"secretKey\" : \"12345678\"\r\n}\r\n","source":"file","accessKey":"abc","secretKey":"12345678"},
 {"id":"V05","content":"{\"accessKey\":\"zzz\",\"accessKey\":\"abc\",\"secretKey\":\"12345678\"}\n","source":"literal","reason":"malformed"},
 {"id":"V06","content":"","source":"literal","reason":"malformed"},
 {"id":"V07","content":"accessKey=abc\n","source":"literal","reason":"malformed"},
 {"id":"V08","content":"\f{\"accessKey\":\"abc\",\"secretKey\":\"12345678\"}\n","source":"literal","reason":"malformed"},
 {"id":"V09","content":"[]","source":"literal","reason":"malformed"},
 {"id":"V10","content":"null","source":"literal","reason":"malformed"},
 {"id":"V11","content":"{\"accessKey\":\"abc\"}","source":"literal","reason":"malformed"},
 {"id":"V12","content":"{\"accessKey\":\"abc\",\"secretKey\":\"12345678\",\"bucket\":\"other-v3\"}","source":"literal","reason":"malformed"},
 {"id":"V13","content":"{\"accessKey\":123,\"secretKey\":\"12345678\"}","source":"literal","reason":"malformed"},
 {"id":"V14","content":"{\"accessKey\":\"ab\",\"secretKey\":\"12345678\"}","source":"literal","reason":"malformed"},
 {"id":"V15","content":"{\"accessKey\":\"abcdefghijklmnopqrstu\",\"secretKey\":\"12345678\"}","source":"literal","reason":"malformed"},
 {"id":"V16","content":"{\"accessKey\":\"abc-def\",\"secretKey\":\"12345678\"}","source":"literal","reason":"malformed"},
 {"id":"V17","content":"{\"accessKey\":\"abc\\n\",\"secretKey\":\"12345678\"}","source":"literal","reason":"malformed"},
 {"id":"V18","content":"{\"accessKey\":\"abc\",\"secretKey\":\"1234567\"}","source":"literal","reason":"malformed"},
 {"id":"V19","content":"{\"accessKey\":\"abc\",\"secretKey\":\"12345678901234567890123456789012345678901\"}","source":"literal","reason":"malformed"},
 {"id":"V20","content":"{\"accessKey\":\"abc\",\"secretKey\":\"1234/678\"}","source":"literal","reason":"malformed"},
 {"id":"V21","content":"{\"secretKey\":\"12345678\",\"accessKey\":\"abc\"}\n","source":"literal","reason":"malformed"},
 {"id":"V22","content":"{\"accessKey\":\"\\u0061bc\",\"secretKey\":\"12345678\"}\n","source":"literal","reason":"malformed"},
 {"id":"V23","content":"{\"accessKey\":\"abc\",\"secretKey\":\"12345678\"}\n\n{}","source":"literal","reason":"malformed"},
 {"id":"V24","content":"{\"accessKey\":\"abc\",\"secretKey\":\"12345678\"}\u0000","source":"literal","reason":"malformed"},
 {"id":"V25","content":"{\"accessKey\":\"abc\",\"secretKey\":\"12345678\"}\u000b","source":"literal","reason":"malformed"},
 {"id":"V26","content":"{\"accessKey\":\"abc\",\"secretKey\":\"12345678\u00e9\"}","source":"literal","reason":"malformed"}
]''')
V03 = next(vector for vector in VECTORS if vector["id"] == "V03")["content"].encode("utf-8")

# The files given as bytes: the answer is a pair of keys or a reason.
BYTE_FILES = {
    "B01": (bytes.fromhex("fffe7b7d"), "malformed"),
    "B02": (V03 + b" " * (MAX_BYTES - len(V03)), ("abc", "12345678")),
    "B03": (V03 + b" " * (MAX_BYTES + 1 - len(V03)), "too large"),
    "B07": (V03.decode("ascii").encode("utf-16-le"), "malformed"),
    "B09": (bytes.fromhex("efbbbf") + V03, "malformed"),
}


@pytest.fixture
def reader(monkeypatch):
    """src/store_credential.py, imported anew."""
    monkeypatch.syspath_prepend(str(SRC))
    monkeypatch.delitem(sys.modules, "store_credential", raising=False)
    return importlib.import_module("store_credential")


def _answers(reader, caplog, path):
    """The reader's answer for `path`, asserted the same through a `log` of
    the test's own and through logging.info with one record at INFO, and the
    one line it logged."""
    lines = []
    answer = reader.load(path=str(path), log=lines.append)
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        assert reader.load(path=str(path)) == answer
    assert len(lines) == 1
    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [(logging.INFO, lines[0])]
    return answer, lines[0]


def _assert_file(answer, line, path, access_key, secret_key):
    assert answer == (access_key, secret_key, "file")
    assert line == f"store credential: file ({path})"


def _assert_literal(answer, line, path, reason):
    assert answer == (LITERAL, LITERAL, "literal")
    assert line == f"store credential: literal ({path}: {reason})"
    assert LITERAL not in line


def test_the_reader_is_the_contracts_text():
    text = READER.read_bytes().replace(b"\r\n", b"\n")
    assert len(text) == READER_BYTES
    assert hashlib.sha256(text).hexdigest() == READER_SHA256


def test_the_vectors_are_twenty_six_with_their_ids_in_order():
    assert [vector["id"] for vector in VECTORS] == [f"V{number:02d}" for number in range(1, 27)]
    assert [vector["id"] for vector in VECTORS if vector["source"] == "file"] == ["V01", "V02", "V03", "V04"]
    assert {vector["reason"] for vector in VECTORS if vector["source"] == "literal"} == {"malformed"}


@pytest.mark.parametrize("vector", VECTORS, ids=[vector["id"] for vector in VECTORS])
def test_a_vector_gives_its_answer_and_one_log_line_without_a_key_or_the_files_bytes(reader, caplog, tmp_path, vector):
    path = tmp_path / "ecld" / "store.json"
    path.parent.mkdir()
    path.write_bytes(vector["content"].encode("utf-8"))
    answer, line = _answers(reader, caplog, path)
    if vector["source"] == "file":
        _assert_file(answer, line, path, vector["accessKey"], vector["secretKey"])
    else:
        _assert_literal(answer, line, path, vector["reason"])
    written = line.replace(str(path), "")
    for held in re.findall(r"[A-Za-z0-9/-]{3,}", vector["content"]) + list(answer[:2]):
        assert held not in written, held


@pytest.mark.parametrize("case", sorted(BYTE_FILES))
def test_a_file_of_these_bytes_gives_its_answer(reader, caplog, tmp_path, case):
    data, expected = BYTE_FILES[case]
    path = tmp_path / "store.json"
    path.write_bytes(data)
    answer, line = _answers(reader, caplog, path)
    if isinstance(expected, tuple):
        _assert_file(answer, line, path, *expected)
    else:
        _assert_literal(answer, line, path, expected)


def test_b04_a_directory_at_the_path_is_unreadable(reader, caplog, tmp_path):
    path = tmp_path / "store.json"
    path.mkdir()
    _assert_literal(*_answers(reader, caplog, path), path, "unreadable")


def test_b05_nothing_at_the_path_is_absent(reader, caplog, tmp_path):
    path = tmp_path / "ecld" / "store.json"
    _assert_literal(*_answers(reader, caplog, path), path, "absent")
    path.parent.mkdir()
    _assert_literal(*_answers(reader, caplog, path), path, "absent")


def test_b06_a_regular_file_as_the_paths_parent_is_absent(reader, caplog, tmp_path):
    (tmp_path / "ecld").write_bytes(V03)
    path = tmp_path / "ecld" / "store.json"
    _assert_literal(*_answers(reader, caplog, path), path, "absent")


def test_b08_of_eight_mebibytes_one_read_of_4097_bytes_is_made(reader, monkeypatch, tmp_path):
    path = tmp_path / "store.json"
    path.write_bytes(b" " * (8 * 1024 * 1024))
    reads = []

    class Counted:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *raised):
            self.handle.close()

        def read(self, size=-1):
            data = self.handle.read(size)
            reads.append((size, len(data)))
            return data

    monkeypatch.setattr(reader, "open", lambda name, mode: Counted(builtins.open(name, mode, buffering=0)),
                        raising=False)
    lines = []
    answer = reader.load(path=str(path), log=lines.append)
    assert len(lines) == 1
    _assert_literal(answer, lines[0], path, "too large")
    assert reads == [(MAX_BYTES + 1, MAX_BYTES + 1)]


def test_called_with_no_argument_the_reader_reads_the_baked_path(reader, caplog):
    assert reader.STORE_CREDENTIAL_PATH == CREDENTIAL_PATH
    parameters = inspect.signature(reader.load).parameters
    assert list(parameters) == ["path", "log"]
    assert parameters["path"].default == CREDENTIAL_PATH and parameters["log"].default is logging.info
    if os.path.lexists(CREDENTIAL_PATH):
        pytest.skip(f"{CREDENTIAL_PATH} exists on this host")
    with caplog.at_level(logging.DEBUG):
        assert reader.load() == (LITERAL, LITERAL, "literal")
    assert [record.getMessage() for record in caplog.records] == [
        "store credential: literal (/run/ecld/store.json: absent)"]


def _files(root):
    return sorted(path for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts)


def test_the_literal_pair_is_in_the_reader_alone():
    holders = [path.relative_to(SECURELOCK).as_posix() for path in _files(SECURELOCK)
               if LITERAL.encode("ascii") in path.read_bytes()]
    assert holders == ["src/store_credential.py"]


def _template():
    return ast.parse(TEMPLATE.read_text(encoding="utf-8"))


def _calls(tree, function):
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == function]


def test_the_template_builds_its_one_store_client_from_the_readers_answer():
    tree = _template()
    imported = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert imported.count("store_credential") == 1
    assert "store_credential" not in [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    loads, clients = _calls(tree, "store_credential.load"), _calls(tree, "SwiftStreamService")
    assert len(loads) == 1 and loads[0].args == [] and loads[0].keywords == []
    assert len(clients) == 1 and clients[0].keywords == []
    # The module is named in that call alone.
    assert [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "store_credential"] == [
        loads[0].func.value]
    guard = next(node for node in tree.body if isinstance(node, ast.If)
                 and isinstance(node.test, ast.Compare) and getattr(node.test.left, "id", "") == "__name__")
    attempt = next(node for node in guard.body if isinstance(node, ast.Try)
                   and any(loads[0] is inner for inner in ast.walk(node)))
    read, construct = attempt.body[0], attempt.body[1]
    assert isinstance(read, ast.Assign) and read.value is loads[0]
    assert [ast.unparse(target) for target in read.targets[0].elts] == ["access_key", "secret_key", "_"]
    assert isinstance(construct, ast.Assign) and construct.value is clients[0]
    assert [ast.unparse(argument) for argument in clients[0].args] == [
        "'etny-swift-stream:9000'", "access_key", "secret_key"]
    # The two keys are named in those two statements alone.
    named = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in ("access_key", "secret_key")]
    assert len(named) == 4
    assert all(any(node is inner for statement in (read, construct) for inner in ast.walk(statement)) for node in named)


def test_no_other_module_of_the_securelock_imports_the_reader():
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        names += [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert "store_credential" not in names, path.name


def test_the_binary_fs_build_declares_the_three_host_paths_in_order():
    lines = (SECURELOCK / "scripts" / "binary-fs-build.sh").read_text(encoding="utf-8").splitlines()
    first = next(index for index, line in enumerate(lines) if line.startswith("EXEC=("))
    last = next(index for index in range(first, len(lines)) if lines[index].endswith(")"))
    assert lines[last - 2:last + 1] == [
        "  --host-path=/etc/resolv.conf \\",
        "  --host-path=/run/ecld \\",
        "  --host-path=/etc/hosts)"]
    assert [index for index, line in enumerate(lines) if "--host-path" in line] == [last - 2, last - 1, last]


def _dockerfile(path):
    return path.read_text(encoding="utf-8").splitlines()


def _instructions(lines, name):
    return [index for index, line in enumerate(lines) if line.split(" ")[0].upper() == name]


def _assert_label_before_entrypoint(lines):
    """The label of a securelock image on the line before the one
    ENTRYPOINT, in the last stage, and no other LABEL instruction."""
    entrypoint = _instructions(lines, "ENTRYPOINT")
    assert len(entrypoint) == 1 and entrypoint[0] > _instructions(lines, "FROM")[-1]
    assert lines[entrypoint[0] - 1] == LABEL
    assert _instructions(lines, "LABEL") == [entrypoint[0] - 1]
    assert not any("cloud.ethernity.store" in line for line in lines)


def test_the_last_stage_of_the_image_holds_the_securelocks_label_before_its_entrypoint():
    _assert_label_before_entrypoint(_dockerfile(SECURELOCK / "Dockerfile.tpl"))
    assert _instructions(_dockerfile(SECURELOCK / "Dockerfile.base.tpl"), "LABEL") == []


def test_no_dockerfile_of_the_image_names_a_user():
    serverless = SECURELOCK.parents[3] / "templates" / "src" / "serverless" / "Dockerfile.serverless"
    for path in (SECURELOCK / "Dockerfile.tpl", SECURELOCK / "Dockerfile.base.tpl", serverless):
        assert _instructions(_dockerfile(path), "USER") == [], path.name


PUT_PATTERNS = ("result.*", "result_code.*", "cert.pem", "esr.*", "state.*", "session.commit.*",
                "session.output.*", "session.ready*", "securelock.*")
PUT_NAME = re.compile("|".join(re.escape(pattern).replace(r"\*", ".*") for pattern in PUT_PATTERNS), re.DOTALL)
KEY16 = "0123456789abcdef"
ADMITTED = (
    "result.txt", "result.txt.securelock", "result.txt.sig.securelock", "result.trustedzone",
    "result.trustedzone.sig", "result_code.txt.securelock", "result_code.txt.sig.securelock",
    "result_code.trustedzone", "result_code.trustedzone.sig", "cert.pem", "esr.authorizations.json",
    f"esr.commit.{KEY16}.7.json", "state.counter.enc", "state.counter.cid", f"state.fence.{KEY16}.enc",
    f"state.fence.{KEY16}.cid", f"session.commit.3.{KEY16}.7.json", "session.output.3.securelock",
    "session.output.3.sig.securelock", "session.output.3", "session.output.3.sig", "session.ready.securelock",
    "session.ready.sig.securelock", "session.ready", "session.ready.sig", "securelock.heartbeat.securelock",
    "securelock.ready.securelock", "securelock.ready.sig.securelock", "securelock.heartbeat", "securelock.ready",
    "securelock.ready.sig")
REFUSED = (
    ".env", "payload.etny", "input.txt", "transaction.txt", "payload.etny.securelock", "payload.securelock",
    "input.txt.securelock", "task.securelock", "caller.securelock", "session.config.securelock",
    "session.input.0", "session.input.0.securelock", "session.control.securelock", "session.fence.securelock",
    "session.replay.0", "session.wallet.proof", "payload.etny.links", "x.links")


def _outside(names):
    """The names of `names` no pattern of PUT_PATTERNS matches."""
    return sorted({name for name in names if PUT_NAME.fullmatch(name) is None})


def test_the_patterns_admit_the_securelocks_names_and_no_other_enclaves():
    assert len(PUT_PATTERNS) == 9
    assert _outside(ADMITTED) == []
    assert _outside(REFUSED) == sorted(REFUSED)
    assert _outside(["resultx", "cert.pem.sig", "session.ready2", "securelock", "a/result.txt", "result.a/b"]) == [
        "a/result.txt", "cert.pem.sig", "resultx", "securelock"]


def test_a_plain_task_puts_only_names_of_the_patterns_and_removes_nothing(monkeypatch, capsys, tmp_path):
    run = order._run(monkeypatch, capsys, tmp_path, "ecld.result('done')\n")
    assert run.code == "0"
    assert set(run.store.puts) == {
        "cert.pem", "securelock.ready", "securelock.ready.sig", "securelock.heartbeat",
        "result.trustedzone", "result.trustedzone.sig", "result_code.trustedzone", "result_code.trustedzone.sig"}
    assert _outside(run.store.puts) == [] and run.store.removes == []


def test_a_session_puts_only_names_of_the_patterns_and_removes_nothing(monkeypatch, capsys, tmp_path):
    run = order._run(monkeypatch, capsys, tmp_path, order.SESSION_PAYLOAD, session=order.SESSION_CONFIG,
                     inputs=[order.SESSION_INPUT])
    assert run.code == "0"
    assert {"session.ready", "session.ready.sig", "session.output.0", "session.output.0.sig",
            "result.trustedzone", "cert.pem"} <= set(run.store.puts)
    assert _outside(run.store.puts) == [] and run.store.removes == []


PLAIN_COMMIT = """
from ecld_state import StateRegistry
StateRegistry().commit("counter", lambda s: {"n": 1})
___etny_result___("committed")
"""
STEP_COMMIT = """
from ecld_state import StateRegistry
def on_input(data):
    StateRegistry().commit("a", lambda s: {"balance": 2})
    return "updated"
ecld.on_input = on_input
"""


def test_a_plain_tasks_commit_puts_only_names_of_the_patterns(monkeypatch):
    env = session_env(monkeypatch)
    code, _ = importlib.import_module("etny_exec").execute_task_v3(PLAIN_COMMIT, "")
    assert int(code) == 0
    puts = env.swift.puts
    assert {"state.counter.enc", "state.counter.cid", "esr.authorizations.json"} <= set(puts)
    assert len([name for name in puts if re.fullmatch(r"esr\.commit\.[0-9a-f]{16}\.0\.json", name)]) == 1
    assert _outside(puts) == [] and env.swift.removes == []


def test_a_session_steps_commit_and_a_fence_put_only_names_of_the_patterns(monkeypatch):
    env = session_env(monkeypatch)
    ecld_state, reg = env.ecld_state, env.reg
    ecld_state.set_local_commit_apply(
        lambda enclave, kh, cid, ev, nonce=0: (reg.land_at(reg.block_number + 1),
                                               reg.apply_commit(enclave, kh, cid, ev, nonce)))
    app = FakeApp(env.swift, caller=CALLER, payload=STEP_COMMIT)
    app.stage("session.config.securelock", json.dumps(
        {"order_id": ORDER_ID, "close_after_seconds": 0.4, "input_cutoff_seconds": 0.4, "max_messages": 256}))
    app.stage("session.input.0.securelock", "update")
    # The puts from here on are the securelock's.
    del env.swift.puts[:]
    code, _ = env.exec_mod.SecureLockSession(app).run()
    assert int(code) == 0
    step = list(env.swift.puts)
    assert {"state.a.enc", "state.a.cid", "esr.authorizations.json", "session.output.0", "session.ready"} <= set(step)
    assert len([name for name in step if re.fullmatch(r"session\.commit\.0\.[0-9a-f]{16}\.0\.json", name)]) == 1

    # The node stages the committed object under its CID, from which the fence reads it.
    cid = content(env.swift, "state.a.cid").decode("utf-8")
    env.swift._store[f"{BUCKET}/state.{cid}.enc"] = content(env.swift, "state.a.enc")
    del env.swift.puts[:]
    monkeypatch.setattr(ecld_state, "FENCE_POLL_SECONDS", 0.01)
    monkeypatch.setattr(ecld_state, "FENCE_LANDING_WAIT_SECONDS", 5)
    assert len(ecld_state.StateRegistry().fence(reg.block_number)) == 1
    fence = list(env.swift.puts)
    assert len([name for name in fence if re.fullmatch(r"state\.fence\.[0-9a-f]{16}\.(enc|cid)", name)]) == 2
    assert len([name for name in fence if re.fullmatch(r"esr\.commit\.[0-9a-f]{16}\.1\.json", name)]) == 1
    assert _outside(step + fence) == [] and env.swift.removes == []


# The methods of SwiftStreamService and of its minio client that remove or
# upload; the securelock's puts go through put_file_content.
REMOVALS_AND_UPLOADS = {"delete_file", "delete_files", "delete_bucket", "remove_object", "remove_objects",
                        "remove_bucket", "upload_file", "upload_files", "fput_object", "put_object"}


def test_only_the_store_service_calls_a_removal_or_an_upload_of_the_store_client():
    for path in sorted(p for p in SRC.iterdir() if p.is_file() and p.suffix in (".py", ".tmpl")):
        called = {node.func.attr for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        if path.name == "swift_stream_service.py":
            assert called & REMOVALS_AND_UPLOADS
        else:
            assert called & REMOVALS_AND_UPLOADS == set(), path.name


DIGEST = "sha256:" + "ab" * 32
SIGNED = "81a7f8e8" + "0" * 56


def test_ecld_build_hands_docker_the_reader_the_host_path_and_the_label(monkeypatch, tmp_path):
    (tmp_path / "src" / "serverless").mkdir(parents=True)
    (tmp_path / "src" / "serverless" / "backend.py").write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    (tmp_path / ".config.json").write_text(json.dumps({
        "PROJECT_NAME": "demo", "BLOCKCHAIN_NETWORK": "IOTEX_TESTNET", "DAPP_TYPE": "Pynithy", "VERSION": 1,
        "MEMORY_TO_ALLOCATE": "1G"}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cfg = Config(tmp_path / ".config.json")
    cfg.load()
    monkeypatch.setattr(build, "config", cfg)
    commands = []
    monkeypatch.setattr(build, "run_command",
                        lambda command, redirect_output=False: commands.append((command, Path.cwd())))
    monkeypatch.setattr(build, "get_docker_server_info", lambda: True)
    outputs = {"docker inspect": f"localhost:5000/etny-securelock@{DIGEST}\n", "signed_mrenclave": f"{SIGNED}\n"}
    monkeypatch.setattr(build, "get_command_output",
                        lambda command: next((out for key, out in outputs.items() if key in command), None))

    build.main()

    context = tmp_path / "build" / "securelock"
    reader_text = (context / "src" / "store_credential.py").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(reader_text).hexdigest() == READER_SHA256
    assert "  --host-path=/run/ecld \\" in (context / "scripts" / "binary-fs-build.sh").read_text(
        encoding="utf-8").splitlines()
    rendered = [line for line in _dockerfile(context / "Dockerfile") if line.strip()]
    _assert_label_before_entrypoint(rendered)
    assert rendered[-2:] == [LABEL, 'ENTRYPOINT ["/usr/local/bin/python", "/etny-securelock/securelock.py"]']
    assert not any(re.search(r"__[A-Z][A-Z0-9_]*__", line) for line in rendered)

    # The securelock is built from that Dockerfile, tagged and pushed as built.
    session = cfg.read("SECURELOCK_SESSION")
    image = [(command, cwd) for command, cwd in commands if "etny-securelock" in command
             and "etny-securelock-base" not in command and "etny-securelock-serverless" not in command]
    assert [command for command, _ in image] == [
        f"docker build --build-arg SECURELOCK_SESSION={session} -t etny-securelock:latest .",
        "docker tag etny-securelock localhost:5000/etny-securelock",
        "docker push localhost:5000/etny-securelock"]
    assert os.path.samefile(image[0][1], context)
    assert not any("--label" in command or "--squash" in command for command, _ in commands)
    assert cfg.read("SECURELOCK_IMAGE_DIGEST") == DIGEST


def _publish_module(monkeypatch, tmp_path, network):
    """The publish module with its config over a .config.json in tmp_path,
    the CWD (the module builds an ImageRegistry from it at import)."""
    stored = {"PROJECT_NAME": "demo", "BLOCKCHAIN_NETWORK": network, "DAPP_TYPE": "Pynithy", "VERSION": 1,
              "SECURELOCK_SESSION": "demo_SECURELOCK_V3_testnet_1", "MEMORY_TO_ALLOCATE": "1G"}
    (tmp_path / ".config.json").write_text(json.dumps(stored), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    publish = importlib.import_module("ethernity_cloud_sdk_py.commands.pynithy.publish")
    cfg = Config(tmp_path / ".config.json")
    cfg.load()
    monkeypatch.setattr(publish, "config", cfg)
    return publish


@pytest.mark.parametrize("network, services", [
    ("IOTEX_TESTNET", {"las", "etny-securelock", "etny-trustedzone"}),
    ("BLOXBERG_TESTNET_UNSAFE", {"etny-securelock", "etny-trustedzone"})])
def test_the_composes_ecld_publish_writes_mount_nothing_at_run_ecld_and_name_the_builds_securelock(
        monkeypatch, tmp_path, network, services):
    publish = _publish_module(monkeypatch, tmp_path, network)
    dest = tmp_path / "build" / "securelock" / "run"
    assert publish.update_docker_compose_files(dest, "127.0.0.1:49153") is True
    for name, registry in (("docker-compose.yml", "127.0.0.1:49153"), ("docker-compose-final.yml", "localhost:5000")):
        text = (dest / name).read_text(encoding="utf-8")
        compose = yaml.safe_load(text)
        assert set(compose["services"]) == services
        assert "/run/ecld" not in text and "volumes" not in compose
        assert compose["services"]["etny-securelock"]["image"] == f"{registry}/etny-securelock"
        for enclave in ("etny-securelock", "etny-trustedzone"):
            assert "volumes" not in compose["services"][enclave]
            assert "user" not in compose["services"][enclave]


STORE = os.environ.get("ECLD_TEST_STORE_ENDPOINT", "")
store = pytest.mark.skipif(not STORE, reason="ECLD_TEST_STORE_ENDPOINT names no MinIO (host:port)")
INTEGRATION_BUCKET = "etny-bucket-integration"
PART = 6 * 1024 * 1024


@pytest.fixture
def held(reader, monkeypatch):
    """The store of ECLD_TEST_STORE_ENDPOINT as an enclave reaches it: the
    repository's SwiftStreamService built from the reader's answer for the
    file ECLD_TEST_STORE_CREDENTIAL names, with the bucket and the role of
    that file's user."""
    lines = []
    access_key, secret_key, source = reader.load(path=os.environ["ECLD_TEST_STORE_CREDENTIAL"], log=lines.append)
    assert source == "file", lines
    monkeypatch.delitem(sys.modules, "swift_stream_service", raising=False)
    service = importlib.import_module("swift_stream_service").SwiftStreamService(STORE, access_key, secret_key)
    role = os.environ["ECLD_TEST_STORE_ROLE"]
    assert role in ("trustedzone", "securelock")
    return SimpleNamespace(service=service, bucket=os.environ["ECLD_TEST_STORE_BUCKET"], role=role)


def _code(answer):
    """The S3 error code of a call of SwiftStreamService that failed."""
    status, error = answer
    assert status is False
    return error.code


def _refused(call):
    """The S3 error code a call of the minio client raises."""
    with pytest.raises(Exception) as raised:
        call()
    return getattr(raised.value, "code", type(raised.value).__name__)


def _data(size=10):
    return io.BytesIO(b"\0" * size)


@store
def test_store_the_bucket_answers_its_location_and_its_head(held):
    assert held.service._is_bucket(held.bucket) is True


@store
def test_store_an_absent_object_answers_no_such_key(held):
    absent = "result.absent." + os.urandom(4).hex()
    assert _code(held.service.is_object_in_bucket(held.bucket, absent)) in ("NoSuchKey", "NotFound")
    assert _code(held.service.get_file_content_bytes(held.bucket, absent)) in ("NoSuchKey", "NotFound")


@store
def test_store_a_trustedzones_user_puts_reads_and_removes_any_object(held):
    if held.role != "trustedzone":
        pytest.skip("the role is not trustedzone")
    name = "store-test." + os.urandom(4).hex()
    assert held.service.put_file_content(held.bucket, name, "", _data())[0] is True
    assert held.service.get_file_content_bytes(held.bucket, name) == (True, b"\0" * 10)
    assert held.service.is_object_in_bucket(held.bucket, name)[0] is True
    assert held.service.delete_file(held.bucket, name)[0] is True
    assert _code(held.service.is_object_in_bucket(held.bucket, name)) in ("NoSuchKey", "NotFound")


@store
def test_store_a_securelocks_user_reads_any_object_and_puts_the_names_of_the_patterns(held):
    if held.role != "securelock":
        pytest.skip("the role is not securelock")
    assert held.service.is_object_in_bucket(held.bucket, ".env")[0] is True
    assert held.service.get_file_content_bytes(held.bucket, ".env")[0] is True
    for name in ADMITTED:
        assert held.service.put_file_content(held.bucket, name, "", _data())[0] is True, name
        assert held.service.get_file_content_bytes(held.bucket, name) == (True, b"\0" * 10), name


@store
def test_store_an_object_of_six_mebibytes_is_put_in_parts(held):
    assert held.service.put_file_content(held.bucket, "result.txt", "", _data(PART))[0] is True
    assert held.service.client.stat_object(held.bucket, "result.txt").size == PART


@store
def test_store_a_securelocks_user_removes_nothing_and_puts_no_other_name(held):
    if held.role != "securelock":
        pytest.skip("the role is not securelock")
    assert held.service.put_file_content(held.bucket, "cert.pem", "", _data())[0] is True
    for name in (".env", "cert.pem"):
        assert _code(held.service.delete_file(held.bucket, name)) == "AccessDenied", name
        assert held.service.is_object_in_bucket(held.bucket, name)[0] is True, name
    for name in REFUSED:
        assert _code(held.service.put_file_content(held.bucket, name, "", _data())) == "AccessDenied", name
        assert _code(held.service.put_file_content(held.bucket, name, "", _data(PART))) == "AccessDenied", name


@store
def test_store_every_call_on_the_integration_bucket_is_refused(held):
    if held.bucket == INTEGRATION_BUCKET:
        pytest.skip("the user's bucket is the integration bucket")
    service = held.service
    assert _refused(lambda: service._is_bucket(INTEGRATION_BUCKET)) == "AccessDenied"
    assert _refused(lambda: list(service.client.list_objects(INTEGRATION_BUCKET))) == "AccessDenied"
    assert _code(service.get_file_content_bytes(INTEGRATION_BUCKET, ".env")) == "AccessDenied"
    assert _code(service.is_object_in_bucket(INTEGRATION_BUCKET, ".env")) == "AccessDenied"
    assert _code(service.put_file_content(INTEGRATION_BUCKET, "result.txt", "", _data())) == "AccessDenied"
    assert _code(service.delete_file(INTEGRATION_BUCKET, ".env")) == "AccessDenied"
