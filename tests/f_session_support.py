"""Stand-ins shared by the WS-F tests (test_f_*.py): the securelock sources
loaded fresh, a recording MemSwift, the heartbeat reader of
tests/spec/heartbeat.json, a session environment over esr_local with the
state registry configured, and the securelock app as the session executor
and the probe see it (the trustedzone objects it verifies, the objects it
pushes)."""
import base64
import importlib
import io
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SRC = Path(__file__).resolve().parents[1] / "ethernity_cloud_sdk_py/commands/pynithy/build/securelock/src"
SPEC = Path(__file__).resolve().parent / "spec"
MODULES = ("etny_exec", "esr_wallet", "ecld_state", "esr_local", "securelock_probe",
           "etny_session_exec", "local_api")

ORDER_ID = 4101
CHALLENGE = "k7p2x9qv4m1zr8t3hw6n"
BUCKET = "ecld-local"
CALLER = "0x" + "11" * 20
IDENTITY = b"ecld-local-test-identity-key-0001"


def load_modules(monkeypatch):
    """The securelock modules imported anew, as a dict by name."""
    monkeypatch.syspath_prepend(str(SRC))
    for name in MODULES:
        sys.modules.pop(name, None)
    return {name: importlib.import_module(name) for name in MODULES}


def decode_beat(body_base64, key, aad):
    """heartbeat.json's readerRule: the nonce from the first 12 bytes, the
    record decrypted under the key and AAD, seq equal to the nonce."""
    body = base64.b64decode(body_base64, validate=True)
    assert len(body) == 60
    nonce = body[:12]
    text = AESGCM(key).decrypt(nonce, body[12:], aad.encode("utf-8")).decode("ascii")
    seq = int(text[11:21])
    assert int.from_bytes(nonce, "big") == seq and nonce[:4] == b"\0" * 4
    return {"seq": seq, "busy": int(text[22]), "runs": int(text[24:30]), "phase": text[31], "text": text}


def session_env(monkeypatch, tick=0.02, period=0.02, tail=0.04):
    """The securelock modules with the session tick, the heartbeat period
    and the idle tail shortened, a recording MemSwift, esr_local's registry
    and web3, and ecld_state configured for CALLER with the local commit
    apply installed."""
    m = load_modules(monkeypatch)
    ecld_state, esr_local = m["ecld_state"], m["esr_local"]
    monkeypatch.setattr(m["etny_session_exec"], "TICK_SECONDS", tick)
    monkeypatch.setattr(m["securelock_probe"], "HEARTBEAT_PERIOD_SECONDS", period)
    monkeypatch.setattr(m["securelock_probe"], "IDLE_TAIL_SECONDS", tail)
    swift = recording_swift(esr_local.MemSwift)
    reg = esr_local.MemRegistry()
    web3 = esr_local.MemWeb3(reg)
    ecld_state.configure(identity_priv=IDENTITY, swift_stream_service=swift, bucket=BUCKET,
                         contract_address="0xLOCAL", web3=web3, caller=CALLER)
    ecld_state.set_local_commit_apply(
        lambda enclave, kh, cid, ev, nonce=0: reg.apply_commit(enclave, kh, cid, ev, nonce))
    return SimpleNamespace(ecld_state=ecld_state, exec_mod=m["etny_session_exec"],
                           probe_mod=m["securelock_probe"], swift=swift, reg=reg, web3=web3)


def wait_for(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


def names(swift, prefix):
    """The object names in the store starting with `prefix`, sorted."""
    return sorted(n[len(BUCKET) + 1:] for n in swift._store if n.startswith(f"{BUCKET}/{prefix}"))


def content(swift, name):
    return swift._store[f"{BUCKET}/{name}"]


def recording_swift(mem_swift_class, put_delay=0.0, refuse=()):
    """An esr_local.MemSwift that also keeps every heartbeat body written,
    in order (`beats`), sleeps `put_delay` on every put and refuses the puts
    of the names in `refuse`."""
    swift = mem_swift_class()
    swift.beats = []
    base_put = swift.put_file_content

    def put_file_content(bucket, object_name, prefix, io_obj):
        if object_name in refuse:
            return False, f"{object_name} refused"
        if put_delay:
            time.sleep(put_delay)
        if object_name == "securelock.heartbeat":
            swift.beats.append(io_obj.getvalue().decode("ascii"))
        return base_put(bucket, object_name, prefix, io_obj)

    swift.put_file_content = put_file_content
    return swift


class FakeApp:
    """The securelock as etny_session_exec and securelock_probe use it:
    stage() places a trustedzone object (its decrypted text and a .sig) in
    the bucket; get_and_verify_trustedzone_object answers it, refusing one
    staged with bad_signature=True; encrypt_file_and_push_to_swifstream
    records what the securelock pushes and writes it, with its .sig, to the
    bucket, raising on a refused put."""

    def __init__(self, swift, caller=None, payload="", input_data="", binding=True):
        self.swift_stream_service = swift
        self.etny_bucket = BUCKET
        self.task_binding = {"order_id": ORDER_ID, "challenge": CHALLENGE} if binding else None
        self.task_caller = caller
        self.payload_data = payload
        self.input_data = input_data
        self.probe = None
        self._objects = {}
        self._bad = set()
        self.pushed = []
        self.log = []

    def stage(self, name, text, bad_signature=False):
        self._objects[name] = text
        if bad_signature:
            self._bad.add(name)
        self.swift_stream_service.put_file_content(BUCKET, name + ".sig", "", io.BytesIO(b"sig"))
        self.swift_stream_service.put_file_content(BUCKET, name, "", io.BytesIO(b"enc"))

    def get_and_verify_trustedzone_object(self, name, signed_prefix=""):
        self.log.append(("read", name, signed_prefix))
        if name not in self._objects:
            raise ValueError(f"Failed to get {name} file")
        if name in self._bad:
            raise ValueError(f"trustedzone signature invalid for {name}")
        return self._objects[name]

    def encrypt_file_and_push_to_swifstream(self, data, name, signed_prefix=""):
        self.pushed.append((name, data, signed_prefix))
        self.log.append(("push", name))
        for obj, text in ((name, data), (name + ".sig", "sig:" + signed_prefix)):
            ok, message = self.swift_stream_service.put_file_content(
                BUCKET, obj, "", io.BytesIO(text.encode("utf-8")))
            if not ok:
                raise RuntimeError(message)

    def pushed_data(self, name):
        return next(data for n, data, _ in self.pushed if n == name)

    def pushed_names(self):
        return [n for n, _, _ in self.pushed]
