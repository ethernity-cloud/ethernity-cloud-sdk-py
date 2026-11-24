"""The upload of a securelock session to the Scontain CAS
(commands/pynithy/scontain_session.py) and the client key it is made under,
against tests/cas_stand_in.py: an HTTPS server on a loopback port that
requires a client certificate, records the key each request came under and
keeps sessions by the SCONE rules the upload relies on (the uploader's key
holds a session unless its access_policy names other keys). No request leaves
the machine: every upload is given the stand-in's URL, and a host name lookup
fails the test.

The flows of a publish run through publish._publish itself, on
BLOXBERG_TESTNET_POX10, with the docker steps replaced as in
test_publish_build_gate.py and an image registry that ends the publish right
after the session is posted. The wallet is the 0xee...ee one of
tests/vectors/cas-client-key.json, whose derived public key the vector
holds."""
import ast
import ipaddress
import json
import os
import shutil
import socket
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
import yaml
from cryptography import x509

from ethernity_cloud_sdk_py.commands.pynithy import scontain_session
from ethernity_cloud_sdk_py.commands.pynithy.cas_client_key import cas_client_key
from ethernity_cloud_sdk_py.commands.spinner import Spinner
from tests.cas_stand_in import Cas, Identity, key_of, post
from tests.test_cas_client_key import QUICK
from tests.test_publish_build_gate import DIGEST, SIGNED, _Registry, _docker, _publish_module

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "ethernity_cloud_sdk_py" / "commands" / "pynithy"
TEMPLATE = PACKAGE / "run" / "etny-securelock-test.yaml.tpl"
WALLET_KEY = QUICK["walletPrivateKey"]
WALLET_CAS_KEY = QUICK["publicKeySpkiDer"]
SESSION = "demo_SECURELOCK_V3_bloxberg_testnet_pox10_1"
RECORD = scontain_session.RECORD
UPLOADED = "The session is uploaded under the CAS client key of the publishing wallet"
HANDED_OVER = ("The session's read and update rights now belong to the CAS client key of the publishing wallet: "
               "the next publish uploads under it")


def unused(directory):
    return (f"key.pem and cert.pem of {Path(directory).resolve()} are no longer used: "
            "the session is uploaded under the CAS client key of the publishing wallet")


def under_old_key(directory):
    return (f"The session is uploaded under key.pem and cert.pem of {Path(directory).resolve()}; "
            "its access policy gives its read and update rights to the CAS client key of the publishing wallet")


@pytest.fixture(autouse=True)
def no_host_name_is_resolved(monkeypatch):
    """An address is looked up as the socket module does; a host name ends
    the test."""
    getaddrinfo = socket.getaddrinfo

    def only_addresses(host, *args, **kwargs):
        if isinstance(host, str):
            try:
                ipaddress.ip_address(host)
            except ValueError:
                raise AssertionError(f"this test resolves no host name: {host}") from None
        return getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", only_addresses)


@pytest.fixture(autouse=True)
def no_spinner_thread(monkeypatch):
    """Spinner.spin_till_done without the frames its thread writes: a
    function that exits inside it leaves that thread writing to stdout."""
    monkeypatch.setattr(Spinner, "spin_message", lambda self, message, stop_event: None)


@pytest.fixture
def temp_root(monkeypatch, tmp_path):
    """The directory tempfile makes its directories in, empty."""
    root = tmp_path / "system-temp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


@pytest.fixture
def cas():
    stand_in = Cas()
    yield stand_in
    stand_in.close()


def snapshot(directory):
    """Every file under `directory`, by its path from it, with its bytes."""
    directory = Path(directory)
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def written(directory, before):
    """The files under `directory` created or changed since `before`, none
    removed; and none of them holds a private key."""
    after = snapshot(directory)
    assert set(before) <= set(after), "a file was removed"
    changed = sorted(name for name, content in after.items() if before.get(name) != content)
    for name in changed:
        assert b"PRIVATE KEY" not in after[name], f"{name} holds a private key"
    return changed


def observed_temp(root):
    """What tempfile's directory holds: each entry with its files, their first
    line and, on POSIX, the permission bits of both."""
    seen = {}
    for entry in sorted(Path(root).iterdir()):
        files = {}
        for path in sorted(entry.iterdir()):
            files[path.name] = (path.read_bytes().split(b"\n", 1)[0], stat.S_IMODE(path.stat().st_mode))
        seen[entry.name] = (stat.S_IMODE(entry.stat().st_mode), files)
    return seen


def render(path, predecessor=""):
    """The session of SESSION from the package's template, as publish.py
    renders one for a mainnet."""
    content = TEMPLATE.read_text(encoding="utf-8")
    for token, value in {
        "__PREDECESSOR__": f"predecessor: {predecessor}" if predecessor else "",
        "__MRENCLAVE__": SIGNED, "__ENCLAVE_NAME__": SESSION,
        "__IGNORE_ADVISORIES__": '["INTEL-SA-00615"]', "__ESR_ENVIRONMENT__": "# ESR not enabled",
    }.items():
        content = content.replace(token, value)
    assert "__" not in content
    with open(path, "w") as f:
        f.write(content)
    return Path(path).read_bytes()


def upload(cas, session_file, legacy_dir, recorded=None, predecessor=False, url=None):
    """scontain_session.upload for WALLET_KEY: the response, the values it
    recorded and the lines it logged."""
    records, lines = [], []
    response = scontain_session.upload(
        url=url or cas.url, session_file=str(session_file), predecessor=predecessor, legacy_dir=legacy_dir,
        recorded=recorded, wallet_key=WALLET_KEY, organization=SESSION,
        write_record=records.append, log=lines.append)
    return response, records, lines


# ---- the module ----

def test_the_upload_goes_to_scone_cas_cf_in_a_publish():
    assert scontain_session.SESSION_URL == "https://scone-cas.cf:8081/session"
    assert (RECORD, scontain_session.HANDOVER, scontain_session.WALLET) == ("CAS_CLIENT_KEY", "handover", "wallet")
    tree = ast.parse((PACKAGE / "publish.py").read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and ast.unparse(node.func) == "scontain_session.upload"]
    assert len(calls) == 1 and calls[0].args == []
    assert [(keyword.arg, ast.unparse(keyword.value)) for keyword in calls[0].keywords] == [
        ("url", "scontain_session.SESSION_URL"),
        ("session_file", "'etny-securelock-test.yaml'"),
        ("predecessor", "config.read('PREDECESSOR_HASH_SECURELOCK') != ''"),
        ("legacy_dir", "certs_dir"),
        ("recorded", "config.read(scontain_session.RECORD)"),
        ("wallet_key", "wallet_key"),
        ("organization", "config.read('SECURELOCK_SESSION')"),
        ("write_record", "lambda value: config.write(scontain_session.RECORD, value)"),
        ("log", "log"),
    ]
    # publish.py makes no key, and no string of its code names a key file or
    # the CAS's session endpoint.
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not names & {"generate_private_key", "generate_certificates", "CertificateBuilder", "private_bytes"}
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node) is not None}
    strings = [node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings]
    assert "etny-securelock-test.yaml" in strings
    for text in strings:
        assert ".pem" not in text, text
        assert "scone-cas.cf:8081" not in text, text


@pytest.mark.parametrize("recorded", [None, "", "other", "handover", "wallet"])
@pytest.mark.parametrize("predecessor", [False, True])
@pytest.mark.parametrize("legacy_files", [False, True])
def test_the_projects_old_key_signs_only_the_update_of_a_session_no_record_covers(recorded, predecessor, legacy_files):
    hand_over = predecessor and legacy_files and recorded not in ("handover", "wallet")
    assert scontain_session.upload_key(predecessor, legacy_files, recorded) == ("handover" if hand_over else "wallet")


def test_the_stand_in_requires_a_client_certificate_and_holds_a_session_for_its_uploaders_key(cas, tmp_path):
    body = render(tmp_path / "session.yaml")
    assert post(cas.url, body)[0] == 401
    assert cas.requests[0]["key"] is None
    owner = Identity("owner")
    other = Identity("other")
    status, answer = post(cas.url, body, owner.write(tmp_path / "owner"))
    assert status == 201
    assert cas.requests[1]["key"] == owner.key
    update = render(tmp_path / "session.yaml", answer["hash"])
    assert post(cas.url, update, other.write(tmp_path / "other"))[0] == 403
    assert post(cas.url, body, owner.write(tmp_path / "owner"))[0] == 409
    assert post(cas.url, update, owner.write(tmp_path / "owner"))[0] == 201


def test_a_new_session_is_posted_under_the_wallets_key_from_files_in_a_directory_of_their_own(cas, tmp_path, temp_root):
    project = tmp_path / "project"
    (project / "build" / "securelock" / "run").mkdir(parents=True)
    session_file = project / "build" / "securelock" / "run" / "etny-securelock-test.yaml"
    body = render(session_file)
    before = snapshot(project)
    cas.observe = lambda: observed_temp(temp_root)

    response, records, lines = upload(cas, session_file, project / "build" / "certs")
    assert response.status_code == 201
    request = cas.requests[0]
    assert request["key"] == WALLET_CAS_KEY
    assert (request["method"], request["path"], request["content_type"]) == ("POST", "/session", "application/octet-stream")
    assert request["body"] == body
    assert "access_policy" not in yaml.safe_load(body)

    certificate = request["certificate"]
    assert certificate.subject.rfc4514_string() == f"O={SESSION},ST=Some-State,C=AU"
    assert certificate.issuer == certificate.subject
    certificate.verify_directly_issued_by(certificate)
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    now = datetime.now(timezone.utc)
    assert abs(certificate.not_valid_before_utc - (now - timedelta(days=365))) < timedelta(minutes=10)
    assert abs(certificate.not_valid_after_utc - (now + timedelta(days=2 * 365))) < timedelta(minutes=10)

    # While the request was open: one directory of the upload's own, with the
    # certificate and its key, which its owner alone can read.
    (name, (directory_mode, files)), = request["observed"].items()
    assert name.startswith("ecld-cas-client-")
    assert sorted(files) == ["cert.pem", "key.pem"]
    assert files["cert.pem"][0] == b"-----BEGIN CERTIFICATE-----"
    assert files["key.pem"][0] == b"-----BEGIN PRIVATE KEY-----"
    if os.name == "posix":
        assert directory_mode == 0o700
        assert {mode for _, mode in files.values()} == {0o600}
    # Afterwards the directory is gone, and the project holds nothing new.
    assert list(temp_root.iterdir()) == []
    assert written(project, before) == []
    assert not (project / "build" / "certs").exists()
    assert records == ["wallet"]
    assert lines == [UPLOADED]

    # The update of that session: the same key under another certificate.
    update = render(session_file, response.json()["hash"])
    response, records, lines = upload(cas, session_file, project / "build" / "certs", recorded="wallet", predecessor=True)
    assert response.status_code == 201
    assert cas.requests[1]["key"] == WALLET_CAS_KEY
    assert cas.requests[1]["body"] == update
    assert cas.requests[1]["certificate"] != certificate
    assert (records, lines) == ([], [UPLOADED])
    assert list(temp_root.iterdir()) == []


def test_the_client_files_are_removed_when_the_cas_refuses_and_when_it_cannot_be_reached(cas, tmp_path, temp_root):
    session_file = tmp_path / "session.yaml"
    # A session another key holds: the CAS refuses the update.
    status, answer = post(cas.url, render(session_file), Identity("a lost key").write(tmp_path / "lost"))
    assert status == 201
    render(session_file, answer["hash"])
    cas.observe = lambda: sorted(path.name for path in temp_root.rglob("*.pem"))
    response, records, lines = upload(cas, session_file, tmp_path / "certs", predecessor=True)
    assert response.status_code == 403
    assert cas.requests[1]["key"] == WALLET_CAS_KEY
    assert cas.requests[1]["observed"] == ["cert.pem", "key.pem"]
    assert (records, lines) == ([], [UPLOADED])
    assert list(temp_root.iterdir()) == []

    # A port nothing listens on: the upload raises and leaves no file.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    with pytest.raises(requests.ConnectionError):
        upload(cas, session_file, tmp_path / "certs", predecessor=True, url=f"https://127.0.0.1:{port}/session")
    assert list(temp_root.iterdir()) == []
    assert len(cas.requests) == 2


def test_an_answer_without_the_sessions_hash_records_no_key(cas, tmp_path, temp_root):
    cas.omit_hash = True
    session_file = tmp_path / "session.yaml"
    render(session_file)
    response, records, lines = upload(cas, session_file, tmp_path / "certs")
    assert response.status_code == 201 and "hash" not in response.json()
    assert cas.requests[0]["key"] == WALLET_CAS_KEY
    assert (records, lines) == ([], [UPLOADED])

    legacy = Identity("an SDK up to 1.9.1")
    legacy.write(tmp_path / "certs")
    cas.sessions[SESSION] = {"hash": "cd" * 32, "creator": legacy.key, "read": [legacy.key], "update": [legacy.key]}
    render(session_file, "cd" * 32)
    response, records, lines = upload(cas, session_file, tmp_path / "certs", predecessor=True)
    assert response.status_code == 201
    assert cas.requests[1]["key"] == legacy.key
    assert (records, lines) == ([], [under_old_key(tmp_path / "certs")])


def test_the_access_policy_is_two_yaml_lists_of_one_certificate_each(tmp_path):
    named = Identity("named")
    policy = scontain_session.access_policy(named.cert_pem.decode("ascii"))
    assert "\r" not in policy and policy.endswith("-----END CERTIFICATE-----\n")
    session = yaml.safe_load(render(tmp_path / "session.yaml").decode("utf-8") + "\n" + policy)
    assert session["name"] == SESSION
    assert list(session["access_policy"]) == ["read", "update"]
    for right in ("read", "update"):
        assert [key_of(entry) for entry in session["access_policy"][right]] == [named.key]
        assert session["access_policy"][right][0] == named.cert_pem.decode("ascii")


def test_scontain_session_py_writes_the_session_file_and_the_files_of_its_own_directory_and_removes_that_directory():
    tree = ast.parse((PACKAGE / "scontain_session.py").read_text(encoding="utf-8"))
    calls = [ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)]
    # The two opens of the session file, and the one of a file in the
    # directory tempfile.mkdtemp made.
    opened = sorted(ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and ast.unparse(node.func) in ("open", "os.open", "os.fdopen"))
    assert opened == [
        "open(session_file, 'a')",
        "open(session_file, 'r')",
        "open(session_file, 'rb')",
        "os.fdopen(os.open(os.path.join(directory, name), flags, 384), 'wb')",
        "os.open(os.path.join(directory, name), flags, 384)",
    ]
    made = [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.Call)
            and ast.unparse(node.func) in ("tempfile.mkdtemp", "shutil.rmtree")]
    assert sorted(made) == ["shutil.rmtree(directory)", "tempfile.mkdtemp(prefix='ecld-cas-client-')"]
    for removal in ("os.remove", "os.unlink", "os.rename", "os.replace", "shutil.copy", "shutil.move"):
        assert removal not in calls


# ---- the publish ----

def _copy_run_directory(dest, host):
    """What publish.update_docker_compose_files does before it writes the
    compose files: the run directory made anew from the package's."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(PACKAGE / "run", dest, ignore=shutil.ignore_patterns("__pycache__"))
    return True


@pytest.fixture
def project(monkeypatch, tmp_path, cas, temp_root):
    """A project built for BLOXBERG_TESTNET_POX10 whose publish posts its
    session to the stand-in: the publish module, and the project's
    directory."""
    directory = tmp_path / "project"
    directory.mkdir()
    publish = _publish_module(
        monkeypatch, directory, BLOCKCHAIN_NETWORK="BLOXBERG_TESTNET_POX10", SECURELOCK_SESSION=SESSION,
        MRENCLAVE_SECURELOCK="", PREDECESSOR_HASH_SECURELOCK="")
    _docker(monkeypatch, publish, SIGNED, DIGEST)
    monkeypatch.setattr(publish, "extract_signed_mrenclave", lambda service: SIGNED)
    monkeypatch.setattr(publish, "update_docker_compose_files", _copy_run_directory)
    monkeypatch.setattr(scontain_session, "SESSION_URL", cas.url)
    # While a request is open: the .pem files the project holds.
    cas.observe = lambda: sorted(path.relative_to(directory).as_posix() for path in directory.rglob("*.pem"))
    return publish, directory


def run_publish(publish, directory, capsys):
    """publish._publish from the project's directory, up to the image
    registry's first read or the exit before it: what it printed."""
    os.chdir(directory)
    capsys.readouterr()
    with pytest.raises(SystemExit) as exit_:
        publish._publish(WALLET_KEY, Spinner(), None, None, _Registry("demo", str(directory / "build" / "registry")),
                         (DIGEST, SIGNED))
    assert exit_.value.code == 1
    return capsys.readouterr().out


def posted(out):
    """Whether the publish went on past the session's upload."""
    return "Could not read the image registry: no chain in this test" in out


def remeasured(publish):
    """The project as a publish finds it when the enclave it measures is not
    the one the last publish recorded: the session is posted again."""
    publish.config.write("MRENCLAVE_SECURELOCK", "")


RUN = "build/securelock/run"


def test_a_publish_posts_a_new_session_under_the_wallets_key_and_keeps_no_key(project, cas, capsys, temp_root):
    publish, directory = project
    before = snapshot(directory)
    out = run_publish(publish, directory, capsys)
    assert posted(out)
    assert len(cas.requests) == 1
    request = cas.requests[0]
    assert request["key"] == WALLET_CAS_KEY
    assert request["observed"] == []
    session = yaml.safe_load(request["body"])
    assert session["name"] == SESSION
    assert "predecessor" not in session and "access_policy" not in session
    assert request["body"] == (directory / RUN / "etny-securelock-test.yaml").read_bytes()
    assert publish.config.read(RECORD) == "wallet"
    assert publish.config.read("PREDECESSOR_HASH_SECURELOCK") == cas.sessions[SESSION]["hash"]
    assert json.loads((directory / ".config.json").read_text(encoding="utf-8"))[RECORD] == "wallet"
    assert f"\t✔  {UPLOADED}\n" in out
    assert "no longer used" not in out

    changed = written(directory, before)
    assert ".config.json" in changed and f"{RUN}/etny-securelock-test.yaml" in changed
    assert all(name == ".config.json" or name.startswith(f"{RUN}/") for name in changed)
    assert not (directory / "build" / "certs").exists()
    assert not list(directory.rglob("*.pem"))
    assert list(temp_root.iterdir()) == []


def test_a_project_with_old_key_files_posts_its_next_session_under_the_wallets_key_and_says_so_once(
        project, cas, capsys, temp_root):
    publish, directory = project
    legacy = Identity("an SDK up to 1.9.1")
    legacy.write(directory / "build" / "certs")
    before = snapshot(directory)

    out = run_publish(publish, directory, capsys)
    assert posted(out)
    assert cas.requests[0]["key"] == WALLET_CAS_KEY
    assert "access_policy" not in yaml.safe_load(cas.requests[0]["body"])
    assert publish.config.read(RECORD) == "wallet"
    assert out.count(f"\t✔  {unused(directory / 'build' / 'certs')}\n") == 1

    # The build that follows names a new session; its publish says no more.
    publish.config.write("VERSION", 2)
    publish.config.write("SECURELOCK_SESSION", SESSION[:-1] + "2")
    publish.config.write("PREDECESSOR_HASH_SECURELOCK", "")
    remeasured(publish)
    out = run_publish(publish, directory, capsys)
    assert posted(out)
    assert cas.requests[1]["key"] == WALLET_CAS_KEY
    assert yaml.safe_load(cas.requests[1]["body"])["name"] == SESSION[:-1] + "2"
    assert "no longer used" not in out

    changed = written(directory, before)
    assert not [name for name in changed if name.startswith("build/certs/")]
    assert sorted(path.name for path in directory.rglob("*.pem")) == ["cert.pem", "key.pem"]
    assert [request["observed"] for request in cas.requests] == [["build/certs/cert.pem", "build/certs/key.pem"]] * 2
    assert list(temp_root.iterdir()) == []


def test_an_existing_session_is_handed_over_under_the_old_key_then_updated_under_the_wallets(
        project, cas, capsys, temp_root, tmp_path):
    publish, directory = project
    certs = directory / "build" / "certs"
    legacy = Identity("an SDK up to 1.9.1")
    legacy_files = legacy.write(certs)
    # The session as an SDK up to 1.9.1 created it, under the project's key.pem.
    status, created = post(cas.url, f'name: {SESSION}\nversion: "0.3"\n'.encode(), legacy_files)
    assert status == 201
    publish.config.write("PREDECESSOR_HASH_SECURELOCK", created["hash"])
    before = snapshot(directory)

    # The hand-over: the old key posts, the policy names the wallet's key.
    out = run_publish(publish, directory, capsys)
    assert posted(out)
    request = cas.requests[1]
    assert request["key"] == legacy.key
    assert request["certificate"] == legacy.certificate
    session = yaml.safe_load(request["body"])
    assert session["name"] == SESSION and session["predecessor"] == created["hash"]
    assert list(session["access_policy"]) == ["read", "update"]
    for right in ("read", "update"):
        assert [key_of(entry) for entry in session["access_policy"][right]] == [WALLET_CAS_KEY]
    assert session["access_policy"]["read"] == session["access_policy"]["update"]
    assert list(session)[-1] == "access_policy"
    assert request["body"] == (directory / RUN / "etny-securelock-test.yaml").read_bytes()
    assert cas.sessions[SESSION]["update"] == [WALLET_CAS_KEY] and cas.sessions[SESSION]["read"] == [WALLET_CAS_KEY]
    assert publish.config.read(RECORD) == "handover"
    assert publish.config.read("PREDECESSOR_HASH_SECURELOCK") == cas.sessions[SESSION]["hash"]
    assert f"\t✔  {under_old_key(certs)}\n\t✔  {HANDED_OVER}\n" in out
    assert "no longer used" not in out
    assert list(temp_root.iterdir()) == []

    # The next update goes under the wallet's key and says once that the old
    # files are no longer used.
    handed_over = cas.sessions[SESSION]["hash"]
    remeasured(publish)
    out = run_publish(publish, directory, capsys)
    assert posted(out)
    request = cas.requests[2]
    assert request["key"] == WALLET_CAS_KEY
    session = yaml.safe_load(request["body"])
    assert session["predecessor"] == handed_over and "access_policy" not in session
    assert publish.config.read(RECORD) == "wallet"
    assert f"\t✔  {UPLOADED}\n\t✔  {unused(certs)}\n" in out

    remeasured(publish)
    out = run_publish(publish, directory, capsys)
    assert posted(out)
    assert cas.requests[3]["key"] == WALLET_CAS_KEY
    assert "no longer used" not in out
    assert publish.config.read(RECORD) == "wallet"

    # The old files are as they were, and the project holds no other key.
    changed = written(directory, before)
    assert not [name for name in changed if name.startswith("build/certs/")]
    assert sorted(path.relative_to(directory).as_posix() for path in directory.rglob("*.pem")) == [
        "build/certs/cert.pem", "build/certs/key.pem"]
    assert [request["observed"] for request in cas.requests] == [["build/certs/cert.pem", "build/certs/key.pem"]] * 4
    assert list(temp_root.iterdir()) == []

    # The old key holds the session no longer: the stand-in refuses it.
    current = cas.sessions[SESSION]["hash"]
    status, _ = post(cas.url, f'name: {SESSION}\nversion: "0.3"\npredecessor: {current}\n'.encode(), legacy_files)
    assert status == 403


def test_a_predecessor_without_key_files_is_posted_under_the_wallets_key_whoever_holds_the_session(
        project, cas, capsys, temp_root, tmp_path):
    publish, directory = project

    # The session is the wallet's: the update is accepted.
    assert posted(run_publish(publish, directory, capsys))
    created = cas.sessions[SESSION]["hash"]
    config = json.loads((directory / ".config.json").read_text(encoding="utf-8"))
    del config[RECORD]
    (directory / ".config.json").write_text(json.dumps(config), encoding="utf-8")
    publish.config.load()
    remeasured(publish)
    out = run_publish(publish, directory, capsys)
    assert posted(out)
    request = cas.requests[1]
    assert request["key"] == WALLET_CAS_KEY
    session = yaml.safe_load(request["body"])
    assert session["predecessor"] == created and "access_policy" not in session
    assert publish.config.read(RECORD) == "wallet"
    assert "no longer used" not in out

    # The session is held by a key the project has lost: the CAS refuses, and
    # the publish ends as it does for any update the CAS does not take.
    cas.sessions.clear()
    status, lost = post(cas.url, f'name: {SESSION}\nversion: "0.3"\n'.encode(), Identity("a lost key").write(tmp_path / "lost"))
    assert status == 201
    config = json.loads((directory / ".config.json").read_text(encoding="utf-8"))
    del config[RECORD]
    config["PREDECESSOR_HASH_SECURELOCK"] = lost["hash"]
    (directory / ".config.json").write_text(json.dumps(config), encoding="utf-8")
    publish.config.load()
    remeasured(publish)
    before = snapshot(directory)
    out = run_publish(publish, directory, capsys)
    assert not posted(out)
    request = cas.requests[3]
    assert request["key"] == WALLET_CAS_KEY
    assert request["status"] == 403
    assert "access_policy" not in yaml.safe_load(request["body"])
    assert "Error: Could not update session file for securelock" in out
    assert "Session predecessor is lost and cannot be recovered. Please run ecld-build to increment the version number" in out
    assert publish.config.read(RECORD) is None
    assert publish.config.read("PREDECESSOR_HASH_SECURELOCK") == ""
    assert not (directory / "build" / "certs").exists()
    assert not list(directory.rglob("*.pem"))
    assert [request["observed"] for request in cas.requests] == [[]] * 4
    written(directory, before)
    assert list(temp_root.iterdir()) == []


def test_the_uploads_leave_the_package_as_it_was(project, cas, capsys):
    publish, directory = project

    def listed():
        return {str(path.relative_to(PACKAGE)) for path in PACKAGE.rglob("*") if "__pycache__" not in path.parts}

    before = listed()
    assert posted(run_publish(publish, directory, capsys))
    assert listed() == before
    assert cas_client_key(WALLET_KEY).public_key().public_numbers() == \
        cas.requests[0]["certificate"].public_key().public_numbers()
