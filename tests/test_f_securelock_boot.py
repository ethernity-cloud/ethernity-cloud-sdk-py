"""The securelock template's wiring of WS-F (PHASE-D-PLAN.md F.1, F.3), read
from securelock.py.tmpl itself (the template does not import outside an
enclave build): the boot order (metadata, trustedzone key and task binding
before the payload wait, the probe started before it), the transfer exit
after the idle tail without a result, the tail after _report_failure, the
put of encrypt_file_and_push_to_swifstream raising when refused, and
get_and_verify_trustedzone_object verifying signed_prefix + data."""
import ast
import io
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

TEMPLATE = (Path(__file__).resolve().parents[1] / "ethernity_cloud_sdk_py" / "commands" / "pynithy"
            / "build" / "securelock" / "src" / "securelock.py.tmpl")


def _tree():
    return ast.parse(TEMPLATE.read_text(encoding="utf-8"))


def _class():
    return next(n for n in _tree().body if isinstance(n, ast.ClassDef) and n.name == "EtnySecureLock")


def _method(name):
    return next(n for n in _class().body if isinstance(n, ast.FunctionDef) and n.name == name)


def _main_body():
    guard = next(n for n in _tree().body if isinstance(n, ast.If)
                 and isinstance(n.test, ast.Compare) and getattr(n.test.left, "id", "") == "__name__")
    return guard.body


def _calls(nodes, receiver):
    """Attribute names called on `receiver` under `nodes`, in source order."""
    names = []
    for node in nodes:
        for call in ast.walk(node):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and getattr(call.func.value, "id", "") == receiver):
                names.append((call.lineno, call.func.attr))
    return [name for _, name in sorted(names)]


def _compile(methods, stub_methods=(), namespace=None):
    """EtnySecureLock with only `methods` of the template and `stub_methods`
    (source text), compiled so private names mangle as in the template."""
    body = [n for n in _class().body if isinstance(n, ast.FunctionDef) and n.name in methods]
    assert len(body) == len(methods)
    for src in stub_methods:
        body.extend(ast.parse(src).body)
    module = ast.Module(body=[ast.ClassDef(name="EtnySecureLock", bases=[], keywords=[], body=body,
                                           decorator_list=[], type_params=[])], type_ignores=[])
    ns = {"io": io, "logging": logging, **(namespace or {})}
    exec(compile(ast.fix_missing_locations(module), str(TEMPLATE), "exec"), ns)
    return ns["EtnySecureLock"]


def test_the_boot_order_puts_the_binding_and_the_probe_before_the_payload_wait():
    calls = _calls(_main_body(), "app")
    order = ["prepare_task", "start_probe", "wait_for_payload_and_input", "validate_client_payload",
             "execute", "save_result"]
    positions = [calls.index(name) for name in order]
    assert positions == sorted(positions)
    # The tail after the result on the normal path, and once on the transfer path.
    assert calls.count("end_probe") == 2
    assert calls.index("save_result") < len(calls) - 1 - calls[::-1].index("end_probe")
    prepare = _calls(_method("prepare_task").body, "self")
    assert prepare == ["load_order_metadata", "__get_trustedzone_public_key",
                       "wait_for_task_binding", "__load_task_binding"]
    assert "get_do_request_metadata" not in _calls(_method("validate_client_payload").body, "self")
    assert "task.securelock" in ast.unparse(_method("wait_for_task_binding"))
    start = ast.unparse(_method("start_probe"))
    assert "securelock_probe.SecurelockProbe" in start
    # The probe is kept only once start() wrote the ready object.
    assert start.index("probe.start()") < start.index("self.probe = probe")
    # The binding is loaded once: validate_trustedzone_data no longer reloads it.
    assert "__load_task_binding" not in _calls(_method("validate_trustedzone_data").body, "self")


def test_the_transfer_exits_after_the_tail_without_a_result():
    main = _main_body()
    transfer = next(n for node in main for n in ast.walk(node) if isinstance(n, ast.If)
                    and ast.unparse(n.test) == "app.transferred")
    assert _calls(transfer.body, "app") == ["end_probe"]
    assert "os._exit(0)" in ast.unparse(transfer.body[-1])
    assert "save_result" not in ast.unparse(transfer)
    execute = ast.unparse(_method("execute"))
    assert "if task_result is None:" in execute and "self.transferred = True" in execute
    assert "self.probe.busy(securelock_probe.PHASE_TASK)" in execute and "self.probe.idle()" in execute


def test_report_failure_ends_with_the_idle_tail():
    report = next(n for n in _tree().body if isinstance(n, ast.FunctionDef) and n.name == "_report_failure")
    calls = _calls(report.body, "app")
    assert calls.index("save_result") < calls.index("end_probe")


class _Swift:
    def __init__(self, ok):
        self.ok = ok
        self.puts = []

    def create_bucket(self, bucket):
        return True, ""

    def put_file_content(self, bucket, name, prefix, data):
        self.puts.append(name)
        return (True, "") if self.ok else (False, f"{name} refused")


def _crypto(seen):
    return SimpleNamespace(
        encrypt_with_pub_key=lambda pem, data: data,
        encrypted_data_to_base64_json=lambda data: data,
        sign_data=lambda pem, data: seen.append(data) or b"\x30\x00",
        verify_signature=lambda pub, data, sig: seen.append(data) or True)


def test_a_refused_put_raises_and_an_accepted_one_writes_data_then_signature():
    seen = []
    cls = _compile(["encrypt_file_and_push_to_swifstream"], namespace={"crypto": _crypto(seen)})
    app = SimpleNamespace(trustedzone_public_key_pem="pem", private_key_pem=b"key", etny_bucket="b",
                          swift_stream_service=_Swift(ok=True))
    cls.encrypt_file_and_push_to_swifstream(app, "data", "securelock.ready", signed_prefix="c:4101:securelock.ready:")
    assert app.swift_stream_service.puts == ["securelock.ready", "securelock.ready.sig"]
    assert seen == [b"c:4101:securelock.ready:data"]
    app.swift_stream_service = _Swift(ok=False)
    with pytest.raises(RuntimeError, match="could not write securelock.ready"):
        cls.encrypt_file_and_push_to_swifstream(app, "data", "securelock.ready")


def test_a_trustedzone_object_is_verified_over_the_signed_prefix_and_the_data():
    seen = []
    cls = _compile(
        ["get_and_verify_trustedzone_object"],
        stub_methods=["def __get_file_content_and_decrypt(self, name):\n    return self.store[name]\n"],
        namespace={"crypto": _crypto(seen)})
    app = cls.__new__(cls)
    app.trustedzone_public_key = "00"
    app.store = {"session.fence.securelock": '{"order_id":4101,"bound":1,"kind":"toBlock"}',
                 "session.fence.securelock.sig": "3000"}
    text = app.get_and_verify_trustedzone_object("session.fence.securelock",
                                                 signed_prefix="c:4101:session.fence:")
    assert text == '{"order_id":4101,"bound":1,"kind":"toBlock"}'
    assert seen == [b'c:4101:session.fence:{"order_id":4101,"bound":1,"kind":"toBlock"}']
    app.get_and_verify_trustedzone_object("session.fence.securelock")
    assert seen[-1] == b'{"order_id":4101,"bound":1,"kind":"toBlock"}'
