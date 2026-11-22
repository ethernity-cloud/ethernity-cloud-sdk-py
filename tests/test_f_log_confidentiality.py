"""What the pynithy securelock writes to its stdout and stderr, which the
node's container log keeps and the operator reads, and what it hands the
logging module. The data owner's payload, input and result reach this
enclave encrypted and leave it encrypted for the trustedzone and the data
owner; none of them, nothing of a session's inputs, outputs and summary,
no key and no challenge is written there. Each run below captures stdout and
stderr (capsys) and every record the logging module receives, at any level,
while the template's securelock runs as its __main__ block:

  - a plain task that succeeds;
  - a payload that raises an error quoting its input, a SystemExit without
    [code, data], and a SystemError without and with a chained SystemExit;
  - the top-level error handler: a payload's code that is not an integer,
    and a result str() refuses, whose failure report fails as well;
  - a session's input, output and summary, a session input that does not
    decrypt as UTF-8, a session code that is not an integer, a
    session.config the store takes from a session input, globals whose
    handle raises at the readiness handshake, and a handler that raises a
    BaseException on its thread;
  - the uncaught-exception hooks the template installs;
  - the store credential: an order under no credential file, under a file
    the reader takes and under one it refuses, and, with the file's
    credential, a store that refuses it and a store that ends every
    connection without an answer.

The access key and the secret key of the store, the literal pair among
them, and the credential file's member names are written in no run. Each
run keeps the arguments the template constructs its store client with and
the reader's log lines written before that.

securelock.py.tmpl is rendered for a mainnet network with no
ValidatorRegistry, so it loads the key and the certificate from the session
paths, which the test maps to files of its own, and is executed as __main__
with stand-ins for etny_crypto (AES-256-GCM sealing under the SHA-256 of the
recipient's public key, ECDSA over P-384 with SHA-256), web3 (the PoX and
Image Registry answers of one order), swift_stream_service (esr_local's
MemSwift, recording), exit() and time.sleep (capped at 0.02 s). The
securelock modules are loaded anew from src/; store_credential's open maps
/run/ecld/store.json to a file of the test's own, absent unless the run
gives its bytes. The two runs against a store on the loopback interface
construct the client with the template's own swift_stream_service over
minio, its endpoint replaced by the server's, and are skipped without minio.
The last tests read the template and its modules: they write to stdout and
stderr only through the logging module and print() at the places
KNOWN_PRINTS names, and no comment holds a print or logging call."""
import ast
import base64
import builtins
import hashlib
import http.server
import importlib
import io
import json
import logging
import os
import re
import socketserver
import sys
import threading
import time
import tokenize
import types
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID

from tests.f_session_support import SRC, load_modules, recording_swift

TEMPLATE = SRC / "securelock.py.tmpl"
BUCKET = "etny-suite-v3"
ORDER_ID = 4101
CHALLENGE = "k7p2x9qv4m1zr8t3hw6n"
SESSION_NAME = "etny-suite-securelock"
POX = "0x00000000000000000000000000000000000000A1"
REGISTRY = "0x00000000000000000000000000000000000000B2"
OWNER = "0x" + "11" * 20
NODE = "0x" + "22" * 20
RENDERED = {
    "__NETWORK_TYPE__": "mainnet",
    "__BUCKET_NAME__": BUCKET,
    "__SECURELOCK_SESSION__": SESSION_NAME,
    "__SMART_CONTRACT_ADDRESS__": POX,
    "__IMAGE_REGISTRY_ADDRESS__": REGISTRY,
    "__VALIDATOR_REGISTRY_ADDRESS__": "",
    "__CHAIN_ID__": "8995",
    "__RPC_URL__": "http://rpc.invalid",
}
KEY_FILE = f"/private/{SESSION_NAME}/key.pem"
CERT_FILE = f"/app/{SESSION_NAME}/cert.pem"

PAYLOAD_MARK = "zq9PAYLOADsecret"
INPUT = "zq9INPUTsecret"
RESULT = "zq9RESULTsecret"
CODE = "zq9CODEsecret"
SESSION_INPUT = "zq9SESSIONinput"
SESSION_OUTPUT = "zq9OUTPUTsecret"

# The store client's endpoint in the template, the path the reader reads,
# the literal pair, and a credential file of a user of the test's own.
STORE_ENDPOINT = "etny-swift-stream:9000"
STORE_CREDENTIAL_PATH = "/run/ecld/store.json"
STORE_LITERAL = "swiftstreamadmin"
ACCESS_KEY = "ecldslzq9ACCESSkey01"
SECRET_KEY = "zq9STOREsecret0123456789abcdef01234567"
CREDENTIAL = f'{{"accessKey":"{ACCESS_KEY}","secretKey":"{SECRET_KEY}"}}\n'.encode("ascii")
STORE_SECRETS = (ACCESS_KEY, SECRET_KEY, "accessKey", "secretKey")

SECRETS = (PAYLOAD_MARK, INPUT, RESULT, CODE, SESSION_INPUT, SESSION_OUTPUT, CHALLENGE, STORE_LITERAL)


def _key_and_cert(name):
    key = ec.generate_private_key(ec.SECP384R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return key_pem, cert.public_bytes(serialization.Encoding.PEM)


SL_KEY, SL_CERT = _key_and_cert("SecureLock Enclave")
TZ_KEY, TZ_CERT = _key_and_cert("Trustedzone Enclave")
# The base64 lines of the securelock's private key.
KEY_TEXT = tuple(line for line in SL_KEY.decode("ascii").splitlines() if not line.startswith("-----"))


def _bytes(value):
    return value.encode("utf-8") if isinstance(value, str) else value


def _seal_key(public_key):
    der = public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).digest()


class _Sealing:
    """etny_crypto.etny_crypto as the template calls it."""

    @staticmethod
    def encrypt_with_pub_key(cert_pem, message):
        cert = x509.load_pem_x509_certificate(_bytes(cert_pem))
        nonce = os.urandom(12)
        return AESGCM(_seal_key(cert.public_key())).encrypt(nonce, message, None), nonce

    @staticmethod
    def encrypted_data_to_base64_json(sealed):
        ciphertext, nonce = sealed
        return base64.b64encode(json.dumps({"ciphertext": ciphertext.hex(), "nonce": nonce.hex()}).encode("utf-8"))

    @staticmethod
    def encrypted_data_from_base64_json(data):
        fields = json.loads(base64.b64decode(data).decode("utf-8"))
        return bytes.fromhex(fields["ciphertext"]), bytes.fromhex(fields["nonce"])

    @staticmethod
    def decrypt(private_key_pem, sealed):
        key = serialization.load_pem_private_key(_bytes(private_key_pem), None)
        ciphertext, nonce = sealed
        return AESGCM(_seal_key(key.public_key())).decrypt(nonce, ciphertext, None)

    @staticmethod
    def sign_data(private_key_pem, data):
        return serialization.load_pem_private_key(_bytes(private_key_pem), None).sign(data, ec.ECDSA(hashes.SHA256()))

    @staticmethod
    def verify_signature(public_key_hex, data, sig_der):
        point = bytes.fromhex(public_key_hex[2:] if public_key_hex.startswith("0x") else public_key_hex)
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP384R1(), b"\x04" + point)
        try:
            key.verify(sig_der, data, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:
            return False
        return True


def _sealed_for_securelock(plaintext):
    return _Sealing.encrypted_data_to_base64_json(_Sealing.encrypt_with_pub_key(SL_CERT, _bytes(plaintext)))


def _opened_by_trustedzone(stored):
    return _Sealing.decrypt(TZ_KEY, _Sealing.encrypted_data_from_base64_json(stored)).decode("utf-8")


def _web3_modules(answers):
    """web3 and web3.middleware: Web3(...).eth.contract(address=, abi=)
    answers the calls of `answers[address]` through caller()."""
    class _Contract:
        def __init__(self, functions):
            self._functions = functions

        def caller(self):
            return SimpleNamespace(**self._functions)

    class Web3:
        class HTTPProvider:
            def __init__(self, url):
                self.url = url

        def __init__(self, provider):
            self.middleware_onion = SimpleNamespace(inject=lambda *a, **k: None)
            self.eth = SimpleNamespace(contract=lambda address, abi: _Contract(answers[address]))

    web3 = types.ModuleType("web3")
    middleware = types.ModuleType("web3.middleware")
    middleware.ExtraDataToPOAMiddleware = object()
    web3.Web3 = Web3
    web3.middleware = middleware
    return web3, middleware


class _Records(logging.Handler):
    """Every record the logging module hands the root logger: its message,
    and its formatted text with the record's msg and args."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []
        self.texts = []

    def emit(self, record):
        self.messages.append(record.getMessage())
        self.texts.append(f"{logging.Formatter().format(record)} {record.msg!r} {record.args!r}")


@contextmanager
def _captured(capsys):
    """stdout, stderr and the logging module while the block runs, at any
    level; yields the namespace that holds them after the block. Its
    messages are the logging module's so far while the block runs."""
    capsys.readouterr()
    records = _Records()
    root = logging.getLogger()
    level = root.level
    root.addHandler(records)
    root.setLevel(logging.DEBUG)
    out = SimpleNamespace(messages=records.messages)
    try:
        yield out
    finally:
        root.removeHandler(records)
        root.setLevel(level)
        written = capsys.readouterr()
        out.text = "\n".join([written.out, written.err, *records.texts])


def _assert_nothing_of(run, secrets, what):
    for secret in secrets:
        assert secret not in run.text, f"{what}: {secret} is written"
    for line in KEY_TEXT:
        assert line not in run.text, f"{what}: the identity key is written"


SESSION_CONFIG = {"order_id": ORDER_ID, "close_after_seconds": 1.0, "input_cutoff_seconds": 1.0, "max_messages": 8}


def _run(monkeypatch, capsys, tmp_path, payload, input_data=INPUT, session=None, inputs=(), config_text=None,
         credential=None, client=None):
    """The template's securelock run as __main__ over one order: `payload`
    and `input_data` staged and signed as the trustedzone stages them, the
    order's metadata their checksums; with `session` (a session.config, or
    `config_text` as its plain text) a session whose inputs are `inputs`
    (text, or bytes staged as they are). `credential` is the bytes of the
    file the reader finds at STORE_CREDENTIAL_PATH; without it there is no
    file. The template's SwiftStreamService answers the recording MemSwift
    that holds the order, or what `client` returns for the template's
    arguments. Returns what was written (text, and messages: the logging
    module's), the exit code, the payload's signature, the result and code
    as the trustedzone opens them, the store, and clients: the arguments of
    each SwiftStreamService call with the reader's lines logged before it."""
    mods = load_modules(monkeypatch)
    for name in ("models", "log_text", "store_credential"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    reader = importlib.import_module("store_credential")
    monkeypatch.setattr(mods["etny_session_exec"], "TICK_SECONDS", 0.02)
    monkeypatch.setattr(mods["securelock_probe"], "HEARTBEAT_PERIOD_SECONDS", 0.02)
    monkeypatch.setattr(mods["securelock_probe"], "IDLE_TAIL_SECONDS", 0.04)
    real_sleep = time.sleep
    monkeypatch.setattr(time, "sleep", lambda seconds: real_sleep(min(seconds, 0.02)))
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    for name in ("ESR_CONTRACT_ADDRESS", "ECAS_CAS_QUOTE", "ECAS_ATTEST_SESSION", "ECAS_ENCLAVE_NONCE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ETNY_ORDER_ID", str(ORDER_ID))

    store = recording_swift(mods["esr_local"].MemSwift)
    sha = lambda text: hashlib.sha256(_bytes(text)).hexdigest()

    def put(name, data):
        store._store[f"{BUCKET}/{name}"] = _bytes(data)

    def stage(name, plaintext):
        signature = _Sealing.sign_data(TZ_KEY, _bytes(plaintext)).hex()
        put(name + ".sig", _sealed_for_securelock(signature))
        put(name, _sealed_for_securelock(plaintext))
        return signature

    put(".env", "ETNY_ORDER_ID=%d\n" % ORDER_ID)
    stage("task.securelock", json.dumps({"order_id": ORDER_ID, "challenge": CHALLENGE}))
    payload_signature = stage("payload.securelock", payload)
    stage("input.securelock", input_data)
    if session is not None or config_text is not None:
        stage("session.config.securelock", config_text if config_text is not None else json.dumps(session))
    for seq, data in enumerate(inputs):
        stage(f"session.input.{seq}.securelock", data)

    answers = {
        POX: {
            "_getOrder": lambda order_id: [OWNER, NODE, 7, 9, 1],
            "_getDORequestMetadata": lambda do_req: [
                OWNER, "v3:QmSecurelock:etny-suite-trustedzone:0:0:0x42",
                f"v3:QmPayload:{sha(payload)}", f"v3:QmInput:{sha(input_data)}", NODE],
        },
        REGISTRY: {
            "getLatestTrustedZoneImageCertPublicKey":
                lambda name, version: ("QmTrustedzone", TZ_CERT.decode("ascii"), "QmCompose"),
        },
    }
    web3, middleware = _web3_modules(answers)
    crypto = types.ModuleType("etny_crypto")
    crypto.etny_crypto = _Sealing
    clients, capture = [], []

    def service(*args, **kwargs):
        logged = [message for message in capture[0].messages if message.startswith("store credential:")]
        clients.append(SimpleNamespace(args=args, kwargs=kwargs, logged=logged))
        return store if client is None else client(*args, **kwargs)

    swift = types.ModuleType("swift_stream_service")
    swift.SwiftStreamService = service
    for name, module in (("web3", web3), ("web3.middleware", middleware),
                         ("etny_crypto", crypto), ("swift_stream_service", swift)):
        monkeypatch.setitem(sys.modules, name, module)

    files = {KEY_FILE: tmp_path / "key.pem", CERT_FILE: tmp_path / "cert.pem",
             STORE_CREDENTIAL_PATH: tmp_path / "ecld" / "store.json"}
    files[KEY_FILE].write_bytes(SL_KEY)
    files[CERT_FILE].write_bytes(SL_CERT)
    if credential is not None:
        files[STORE_CREDENTIAL_PATH].parent.mkdir()
        files[STORE_CREDENTIAL_PATH].write_bytes(credential)
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda path: path in files or real_exists(path))

    def open_mapped(path, *args, **kwargs):
        return builtins.open(files.get(path, path), *args, **kwargs)

    assert reader.STORE_CREDENTIAL_PATH == STORE_CREDENTIAL_PATH
    monkeypatch.setattr(reader, "open", open_mapped, raising=False)

    def exit_with(code=None):
        raise SystemExit(code)

    source = TEMPLATE.read_text(encoding="utf-8")
    for token, value in RENDERED.items():
        source = source.replace(token, value)
    assert not re.search(r"__[A-Z][A-Z0-9_]*__", source), "every token of the template is rendered"
    namespace = {"__name__": "__main__", "__file__": str(SRC / "securelock.py"),
                 "__builtins__": builtins, "open": open_mapped, "exit": exit_with}
    with _captured(capsys) as run:
        capture.append(run)
        try:
            exec(compile(source, str(TEMPLATE), "exec"), namespace)
            run.exit_code = None
        except SystemExit as stop:
            run.exit_code = stop.code
    run.store = store
    run.clients = clients
    run.payload_signature = payload_signature
    stored = lambda name: store._store.get(f"{BUCKET}/{name}")
    run.result = _opened_by_trustedzone(stored("result.trustedzone")) if stored("result.trustedzone") else None
    run.code = _opened_by_trustedzone(stored("result_code.trustedzone")) if stored("result_code.trustedzone") else None
    run.opened = lambda name: _opened_by_trustedzone(stored(name))
    return run


def _result_line(code, text):
    data = text.encode("utf-8")
    return f"TASK RESULT: code {code}, {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}"


def _secrets_of(run):
    return SECRETS + (run.payload_signature,)


def test_a_plain_task_that_succeeds_writes_its_code_length_and_sha256_alone(monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path, f"'{PAYLOAD_MARK}'\necld.result('{RESULT}:' + ecld.input)\n")
    _assert_nothing_of(run, _secrets_of(run), "a SUCCESS")
    assert run.exit_code is None and run.code == "0"
    envelope = json.loads(run.result)
    assert envelope["data"] == f"{RESULT}:{INPUT}"
    assert (envelope["order_id"], envelope["challenge"]) == (ORDER_ID, CHALLENGE)
    assert _result_line(0, run.result) in run.messages
    assert "Finished the execution" in run.messages


def test_a_payload_error_quoting_its_input_is_logged_by_class_and_stays_in_the_result(monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path,
               f"'{PAYLOAD_MARK}'\nraise ValueError('the input ' + ecld.input + ' is refused')\n")
    _assert_nothing_of(run, _secrets_of(run), "an error quoting the input")
    assert run.code == "4"
    assert run.result.startswith(f"BASE_EXCEPTION: ValueError('the input {INPUT} is refused')\nTraceback")
    assert "BaseException in payload execution: ValueError" in run.messages
    assert _result_line(4, run.result) in run.messages


def test_a_system_exit_without_code_and_data_is_logged_by_class(monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path, f"'{PAYLOAD_MARK}'\nquit([ecld.input])\n")
    _assert_nothing_of(run, _secrets_of(run), "a SystemExit without [code, data]")
    assert run.code == "1"
    assert run.result.startswith(f"SYSTEM_ERROR: SystemExit(['{INPUT}'])\nTraceback")
    assert "SystemExit without a [code, data] result payload: SystemExit" in run.messages


def test_a_system_error_is_logged_by_class(monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path,
               f"'{PAYLOAD_MARK}'\nraise SystemError('native fault over ' + ecld.input)\n")
    _assert_nothing_of(run, _secrets_of(run), "a SystemError")
    assert run.code == "1"
    assert run.result.startswith(f"SYSTEM_ERROR: SystemError('native fault over {INPUT}')\nTraceback")
    assert "SystemError in payload execution: SystemError" in run.messages


RECOVERED = """'{mark}'
try:
    quit([{code}, '{result}'])
except SystemExit:
    raise SystemError('native fault over ' + ecld.input)
"""


def test_a_system_error_over_a_chained_result_is_logged_by_class_without_the_code(monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path, RECOVERED.format(mark=PAYLOAD_MARK, code=0, result=RESULT))
    _assert_nothing_of(run, _secrets_of(run), "a SystemError over a chained SystemExit")
    assert (run.code, run.result) == ("0", RESULT)
    assert ("SystemError raised during result unwind; recovered task result from chained "
            "SystemExit. Native error: SystemError") in run.messages


def test_a_chained_code_that_is_not_an_integer_is_logged_neither_by_the_recovery_nor_by_the_top_level_handler(
        monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path, RECOVERED.format(mark=PAYLOAD_MARK, code=f"'{CODE}'", result=RESULT))
    _assert_nothing_of(run, _secrets_of(run), "a chained code that is not an integer")
    assert run.exit_code == 1 and run.code == "1"
    assert run.result == f"ENCLAVE ERROR: invalid literal for int() with base 10: '{CODE}'"
    assert ("SystemError raised during result unwind; recovered task result from chained "
            "SystemExit. Native error: SystemError") in run.messages
    assert "Error: ValueError" in run.messages


def test_the_top_level_handler_logs_a_code_that_is_not_an_integer_by_class(monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path, f"'{PAYLOAD_MARK}'\nquit(['{CODE}', '{RESULT}'])\n")
    _assert_nothing_of(run, _secrets_of(run), "a code that is not an integer")
    assert run.exit_code == 1 and run.code == "1"
    assert run.result == f"ENCLAVE ERROR: invalid literal for int() with base 10: '{CODE}'"
    assert "Error: ValueError" in run.messages
    assert "failure reported (task_code=1)" in run.messages


OPAQUE = """'{mark}'
class _Opaque:
    def __str__(self):
        raise ValueError('{result}')
quit([0, _Opaque()])
"""


def test_a_result_str_refuses_is_logged_as_not_text_and_its_errors_by_class(monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path, OPAQUE.format(mark=PAYLOAD_MARK, result=RESULT))
    _assert_nothing_of(run, _secrets_of(run), "a result str() refuses")
    assert run.exit_code == 1 and run.result is None
    assert "TASK RESULT: code 0, not text" in run.messages
    assert "Error: ValueError" in run.messages
    assert "could not report the failure result: ValueError" in run.messages


SESSION_PAYLOAD = f"""'{PAYLOAD_MARK}'
def _reply(data):
    return 'reply to ' + data + ': {SESSION_OUTPUT}'
ecld.on_input = _reply
"""


def test_a_session_writes_neither_its_input_its_output_nor_its_summary(monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path, SESSION_PAYLOAD, session=SESSION_CONFIG, inputs=[SESSION_INPUT])
    _assert_nothing_of(run, _secrets_of(run), "a session")
    output = json.loads(run.opened("session.output.0"))
    assert (output["ack"], output["code"], output["data"]) == (0, 0, f"reply to {SESSION_INPUT}: {SESSION_OUTPUT}")
    summary = json.loads(run.result)
    assert summary == {"session": "v1", "reason": "RUNNING_PERIOD_COMPLETE",
                       "seen": 1, "processed": 1, "emitted": 1, "unprocessed": []}
    assert run.result not in run.text and str(summary) not in run.text
    assert "session: finalizing" in run.messages
    assert _result_line(0, run.result) in run.messages


def test_a_session_input_that_does_not_decrypt_as_utf8_is_logged_by_class(monkeypatch, capsys, tmp_path):
    plaintext = SESSION_INPUT.encode("utf-8") + b"\xc3\x28"
    with pytest.raises(UnicodeDecodeError) as refused:
        plaintext.decode("utf-8")
    config = dict(SESSION_CONFIG, close_after_seconds=0.3, input_cutoff_seconds=0.3)
    run = _run(monkeypatch, capsys, tmp_path, SESSION_PAYLOAD, session=config, inputs=[plaintext])
    _assert_nothing_of(run, _secrets_of(run) + (str(refused.value),), "an input that is not UTF-8")
    assert "session: input 0 failed verification: UnicodeDecodeError" in run.messages
    assert json.loads(run.result)["seen"] == 0


def test_a_session_code_that_is_not_an_integer_is_logged_by_class(monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path, f"'{PAYLOAD_MARK}'\nquit(['{CODE}', 'x'])\n", session=SESSION_CONFIG)
    _assert_nothing_of(run, _secrets_of(run), "a session code that is not an integer")
    assert run.code == "1"
    assert run.result == f"SESSION EXECUTOR ERROR: invalid literal for int() with base 10: '{CODE}'"
    assert "session executor failed: ValueError" in run.messages


def test_a_session_config_taken_from_a_session_input_is_logged_by_class(monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path, SESSION_PAYLOAD, config_text=json.dumps({"order_id": SESSION_INPUT}))
    _assert_nothing_of(run, _secrets_of(run), "a session input as the session.config")
    assert run.code == "1"
    assert run.result == f"SESSION EXECUTOR ERROR: invalid literal for int() with base 10: '{SESSION_INPUT}'"
    assert "session executor failed: ValueError" in run.messages


RAISING_HANDLE = f"""'{PAYLOAD_MARK}'
class _Handle:
    def __getattribute__(self, name):
        raise LookupError('the input ' + ___etny_data_set___ + ' is refused')
ecld = _Handle()
"""


def test_a_readiness_handshake_over_globals_that_raise_is_logged_by_class(monkeypatch, capsys, tmp_path):
    config = dict(SESSION_CONFIG, close_after_seconds=0.3, input_cutoff_seconds=0.3)
    run = _run(monkeypatch, capsys, tmp_path, RAISING_HANDLE, session=config)
    _assert_nothing_of(run, _secrets_of(run), "a readiness handshake that fails")
    assert "session: could not publish readiness: LookupError" in run.messages
    assert json.loads(run.result)["reason"] == "RUNNING_PERIOD_COMPLETE"


THREAD_REFUSAL = f"""'{PAYLOAD_MARK}'
class _Refusal(BaseException):
    pass
def _reply(data):
    raise _Refusal('refused ' + data)
ecld.on_input = _reply
"""


def test_a_handler_raising_a_base_exception_on_its_thread_is_logged_by_class(monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path, THREAD_REFUSAL, session=SESSION_CONFIG, inputs=[SESSION_INPUT])
    _assert_nothing_of(run, _secrets_of(run), "a handler's BaseException")
    assert "uncaught exception in a thread: _Refusal" in run.messages
    assert json.loads(run.opened("session.output.0"))["data"] == "SESSION HANDLER LOST"


def test_the_uncaught_exception_hooks_log_the_class_alone(monkeypatch, capsys, tmp_path):
    _run(monkeypatch, capsys, tmp_path, f"'{PAYLOAD_MARK}'\necld.result('{RESULT}')\n")

    def raised(error):
        try:
            raise error
        except BaseException as caught:
            return caught

    main_error = raised(ValueError(f"the input {INPUT} is refused"))
    thread_error = raised(KeyError(SESSION_INPUT))
    thread_exit = raised(SystemExit(RESULT))
    thread = threading.current_thread()
    with _captured(capsys) as hooks:
        sys.excepthook(type(main_error), main_error, main_error.__traceback__)
        threading.excepthook(threading.ExceptHookArgs(
            (type(thread_error), thread_error, thread_error.__traceback__, thread)))
        threading.excepthook(threading.ExceptHookArgs(
            (type(thread_exit), thread_exit, thread_exit.__traceback__, thread)))
    _assert_nothing_of(hooks, SECRETS, "the uncaught-exception hooks")
    assert hooks.messages == ["uncaught exception: ValueError", "uncaught exception in a thread: KeyError"]


def _credential_lines(run):
    return [message for message in run.messages if message.startswith("store credential:")]


def _clients(run):
    return [(made.args, made.kwargs, made.logged) for made in run.clients]


def test_an_order_under_no_credential_file_runs_on_the_literal_pair_and_logs_its_source_alone(
        monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path, f"'{PAYLOAD_MARK}'\necld.result('{RESULT}')\n")
    _assert_nothing_of(run, _secrets_of(run) + STORE_SECRETS, "an order under no credential file")
    assert run.code == "0"
    line = f"store credential: literal ({STORE_CREDENTIAL_PATH}: absent)"
    assert _credential_lines(run) == [line]
    assert _clients(run) == [((STORE_ENDPOINT, STORE_LITERAL, STORE_LITERAL), {}, [line])]


# The payload's result is the text values of its globals.
CONTEXT = f"""'{PAYLOAD_MARK}'
ecld.result(repr(sorted(item for item in globals().items() if isinstance(item[1], str))))
"""


def test_an_order_under_a_credential_file_writes_neither_key_and_holds_them_in_the_store_client_alone(
        monkeypatch, capsys, tmp_path):
    run = _run(monkeypatch, capsys, tmp_path, CONTEXT, credential=CREDENTIAL)
    _assert_nothing_of(run, _secrets_of(run) + STORE_SECRETS, "an order under a credential file")
    assert run.code == "0"
    line = f"store credential: file ({STORE_CREDENTIAL_PATH})"
    assert _credential_lines(run) == [line]
    assert _clients(run) == [((STORE_ENDPOINT, ACCESS_KEY, SECRET_KEY), {}, [line])]
    context = json.loads(run.result)["data"]
    assert INPUT in context
    for key in (ACCESS_KEY, SECRET_KEY):
        assert key not in context, "the payload's globals hold a key"
        assert not any(key in name or key in value for name, value in os.environ.items())
        assert not any(key.encode("ascii") in data for data in run.store._store.values())


def test_a_credential_file_the_reader_refuses_is_logged_by_its_reason_and_the_order_runs_on_the_literal_pair(
        monkeypatch, capsys, tmp_path):
    named = f'{{"accessKey":"{ACCESS_KEY}","secretKey":"{SECRET_KEY}","bucket":"other-v3"}}\n'.encode("ascii")
    run = _run(monkeypatch, capsys, tmp_path, f"'{PAYLOAD_MARK}'\necld.result('{RESULT}')\n", credential=named)
    _assert_nothing_of(run, _secrets_of(run) + STORE_SECRETS + ("other-v3",), "a file the reader refuses")
    line = f"store credential: literal ({STORE_CREDENTIAL_PATH}: malformed)"
    assert _credential_lines(run) == [line]
    assert _clients(run) == [((STORE_ENDPOINT, STORE_LITERAL, STORE_LITERAL), {}, [line])]
    assert run.code == "0"
    assert all(name.startswith(f"{BUCKET}/") for name in run.store._store)


REFUSAL = (b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>InvalidAccessKeyId</Code>'
           b"<Message>The Access Key Id you provided does not exist in our records.</Message>"
           b"<Resource>/</Resource><RequestId>17A9F3C2</RequestId><HostId>e3b0c442</HostId></Error>")


class _Refusing(http.server.BaseHTTPRequestHandler):
    """A store that answers every request as MinIO answers an access key it
    does not hold; the server keeps each request's method, path and
    Authorization header."""

    def _refuse(self):
        self.server.requests.append((self.command, self.path, self.headers.get("Authorization", "")))
        self.send_response(403)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(REFUSAL)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(REFUSAL)

    do_GET = do_HEAD = do_PUT = do_POST = do_DELETE = _refuse

    def log_message(self, format, *args):
        pass


class _Closing(socketserver.BaseRequestHandler):
    """A store that ends every connection without an answer; the server
    keeps the address of each."""

    def handle(self):
        self.server.connections.append(self.client_address)


@contextmanager
def _serving(server):
    """`server` answering on the loopback interface while the block runs."""
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _minio_client(monkeypatch, server):
    """The template's own SwiftStreamService (swift_stream_service.py over
    minio) for the credential it is given, at `server`'s address."""
    pytest.importorskip("minio")
    monkeypatch.syspath_prepend(str(SRC))
    monkeypatch.delitem(sys.modules, "swift_stream_service", raising=False)
    module = importlib.import_module("swift_stream_service")
    del sys.modules["swift_stream_service"]
    host, port = server.server_address
    return lambda endpoint, access_key, secret_key: module.SwiftStreamService(
        f"{host}:{port}", access_key, secret_key)


def test_a_store_that_refuses_the_files_credential_is_asked_under_no_other_and_neither_key_is_written(
        monkeypatch, capsys, tmp_path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Refusing)
    server.requests = []
    with _serving(server):
        run = _run(monkeypatch, capsys, tmp_path, f"'{PAYLOAD_MARK}'\necld.result('{RESULT}')\n",
                   credential=CREDENTIAL, client=_minio_client(monkeypatch, server))
    _assert_nothing_of(run, _secrets_of(run) + STORE_SECRETS, "a store that refuses the credential")
    line = f"store credential: file ({STORE_CREDENTIAL_PATH})"
    assert _credential_lines(run) == [line]
    assert _clients(run) == [((STORE_ENDPOINT, ACCESS_KEY, SECRET_KEY), {}, [line])]
    assert run.exit_code == 0
    assert "No .env in SwiftStream; public key emitted, exiting (cert-harvest mode)" in run.messages
    assert server.requests and run.store.puts == []
    for method, path, authorization in server.requests:
        assert f"Credential={ACCESS_KEY}/" in authorization, (method, path)
        assert STORE_LITERAL not in authorization, (method, path)


def test_a_store_that_does_not_answer_is_logged_without_either_key(monkeypatch, capsys, tmp_path):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Closing)
    server.connections = []
    with _serving(server):
        run = _run(monkeypatch, capsys, tmp_path, f"'{PAYLOAD_MARK}'\necld.result('{RESULT}')\n",
                   credential=CREDENTIAL, client=_minio_client(monkeypatch, server))
    _assert_nothing_of(run, _secrets_of(run) + STORE_SECRETS, "a store that does not answer")
    line = f"store credential: file ({STORE_CREDENTIAL_PATH})"
    assert _credential_lines(run) == [line]
    assert _clients(run) == [((STORE_ENDPOINT, ACCESS_KEY, SECRET_KEY), {}, [line])]
    assert run.exit_code == 0 and server.connections and run.store.puts == []
    unavailable = [message for message in run.messages if message.startswith("SwiftStream unavailable (")]
    assert len(unavailable) == 1
    assert unavailable[0].endswith("); public key emitted, exiting (cert-harvest mode)")


def test_error_class_and_result_digest(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    monkeypatch.delitem(sys.modules, "log_text", raising=False)
    import log_text

    class StateFault(Exception):
        pass

    class _Named(type):
        @property
        def __name__(cls):
            raise ValueError(INPUT)

    class _Opaque(metaclass=_Named):
        pass

    class _Text:
        def __str__(self):
            raise ValueError(RESULT)

    assert log_text.error_class(StateFault(INPUT)) == "StateFault"
    assert log_text.error_class(INPUT) == "str"
    assert log_text.error_class(None) == "NoneType"
    assert log_text.error_class(_Opaque()) == "unknown class"
    sha = lambda data: hashlib.sha256(data).hexdigest()
    assert log_text.result_digest(RESULT) == f"{len(RESULT)} bytes, sha256 {sha(RESULT.encode())}"
    assert log_text.result_digest(chr(0xe9)) == f"2 bytes, sha256 {sha(chr(0xe9).encode('utf-8'))}"
    assert log_text.result_digest(7) == f"1 bytes, sha256 {sha(b'7')}"
    assert log_text.result_digest(_Text()) == "not text"
    assert log_text.result_digest("\ud800") == "not text"


def _sources():
    return sorted(p for p in SRC.iterdir() if p.is_file() and p.suffix in (".py", ".tmpl"))


# The print() calls of the template and its modules, by file, in source
# order: etny_exec's names of the payload's globals before the payload runs
# (the securelock's functions and the serverless backend's), the local test
# API of ecld-test (local_api, which the securelock does not import), and
# SwiftStreamService's bucket listing and delete error.
KNOWN_PRINTS = {
    "etny_exec.py": [
        "print('Globals keys:', list(globals.keys()))",
        "print('Locals keys:', list(locals.keys()))",
        "print('execve in globals:', 'execve' in globals)",
        "print('execve in locals:', 'execve' in locals)",
    ],
    "local_api.py": [
        "print(f'[local-api] ESR emulation unavailable: {e}')",
        "print(f'[local-api] {self.address_string()} {fmt % args}')",
        "print(f\"[local-api] backend functions: {(funcs if funcs else 'none (stock-enclave mode)')}\")",
        "print(f'[local-api] ESR emulation: ON (in-process, no chain)' + (f'  enclave wallet {wallet}' if wallet else ''))",
        "print(f'[local-api] task caller: {caller_note}')",
        "print('[local-api] ESR state persists in .ecld-esr-local.*.json')",
        "print(f'[local-api] listening on http://{host}:{port}  (POST /v1/task, GET /v1/health)')",
    ],
    "swift_stream_service.py": [
        "print(f'[Error] Error occurred when deleting file: {error}!')",
        "print(bucket.name, bucket.creation_date)",
        "print('-> ', obj.object_name, obj.owner_name)",
    ],
}

# Calls that write to stdout or stderr other than through print() and the
# logging module's records, or that hand the logging module a traceback.
DIRECT_WRITES = {
    ("sys", "stdout"), ("sys", "stderr"), ("sys", "__stdout__"), ("sys", "__stderr__"),
    ("os", "write"), ("traceback", "print_exc"), ("traceback", "print_exception"),
    ("traceback", "print_stack"), ("traceback", "print_tb"), ("traceback", "print_last"),
    ("pprint", "pprint"), ("warnings", "warn"), ("logging", "exception"),
}


def test_the_template_and_its_modules_write_to_stdout_and_stderr_only_through_logging_and_known_prints():
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prints, direct = [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in ("print", "pprint", "breakpoint"):
                    prints.append((node.lineno, node.col_offset, ast.unparse(node)))
                if any(k.arg in ("exc_info", "stack_info") for k in node.keywords):
                    direct.append(ast.unparse(node))
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and (node.value.id, node.attr) in DIRECT_WRITES):
                direct.append(ast.unparse(node))
        assert direct == [], path.name
        assert [text for _, _, text in sorted(prints)] == KNOWN_PRINTS.get(path.name, []), path.name


COMMENTED_CALL = re.compile(
    r"^(print|pprint|breakpoint|logging\.\w+|logger\.\w+|log\.\w+|sys\.std(out|err)\.write|traceback\.print_\w+)\s*\(")


def test_no_comment_of_the_template_and_its_modules_holds_a_print_or_logging_call():
    for path in _sources():
        with open(path, "rb") as source:
            comments = [tok.string.lstrip("#").strip() for tok in tokenize.tokenize(source.readline)
                        if tok.type == tokenize.COMMENT]
        assert [c for c in comments if COMMENTED_CALL.match(c)] == [], path.name
