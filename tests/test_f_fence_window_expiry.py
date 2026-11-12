"""The fence wait's window (tests/spec/session-fence.json timing.rule) in
etny_session_exec: a reserved order (session.config reserved true) waits
for session.fence.securelock for READY_WINDOW_SECONDS whatever else is
staged, a session.input.0 present without it never read, and ends the task
with a SYSTEM_ERROR result on the window's expiry; a fence arriving inside
the window is applied and the inputs follow; an unreserved order (no flag,
or any value but true) waits for nothing."""
import json
import threading
import time

from tests.f_session_support import FakeApp, ORDER_ID, names, session_env, wait_for

PAYLOAD = 'ecld.on_input = lambda data: "answer:" + data\n'
READY_WINDOW = 0.1


def _app(env, reserved, close_after=5):
    config = {"order_id": ORDER_ID, "close_after_seconds": close_after, "input_cutoff_seconds": close_after,
              "max_messages": 256}
    if reserved is not None:
        config["reserved"] = reserved
    app = FakeApp(env.swift, payload=PAYLOAD)
    app.stage("session.config.securelock", json.dumps(config))
    return app


def test_a_reserved_order_ends_on_the_windows_expiry_without_reading_its_input(monkeypatch):
    env = session_env(monkeypatch)
    monkeypatch.setattr(env.exec_mod, "READY_WINDOW_SECONDS", READY_WINDOW)
    app = _app(env, reserved=True)
    app.stage("session.input.0.securelock", "hello")
    session = env.exec_mod.SecureLockSession(app)
    started = time.monotonic()
    code, result = session.run()
    assert time.monotonic() - started >= READY_WINDOW
    assert int(code) == int(env.exec_mod.TaskStatus.SYSTEM_ERROR)
    assert result == f"SESSION FENCE ERROR: session.fence not staged within {READY_WINDOW} s"
    assert session.reason == "FENCE_NOT_RECEIVED" and session.seen == 0
    assert [e for e in app.log if e[0] == "read" and e[1].startswith("session.input")] == []
    assert names(env.swift, "session.output.") == [] and "session.ready" in app.pushed_names()


def test_a_fence_inside_the_window_is_applied_and_the_inputs_follow(monkeypatch):
    env = session_env(monkeypatch)
    monkeypatch.setattr(env.exec_mod, "READY_WINDOW_SECONDS", 2.0)
    app = _app(env, reserved=True, close_after=0.6)
    app.stage("session.input.0.securelock", "hello")
    session = env.exec_mod.SecureLockSession(app)
    box = []
    thread = threading.Thread(target=lambda: box.append(session.run()), daemon=True)
    thread.start()
    wait_for(lambda: "session.ready" in app.pushed_names())
    time.sleep(0.1)
    assert session.seen == 0
    app.stage("session.fence.securelock", json.dumps({"order_id": ORDER_ID, "bound": 0, "kind": "none"},
                                                     separators=(",", ":")))
    thread.join(5)
    assert not thread.is_alive()
    code, result = box[0]
    assert (int(code), json.loads(result)["reason"], json.loads(result)["emitted"]) == (0, "RUNNING_PERIOD_COMPLETE", 1)
    assert ("read", "session.fence.securelock", f"{app.task_binding['challenge']}:{ORDER_ID}:session.fence:") in app.log
    assert json.loads(app.pushed_data("session.output.0"))["data"] == "answer:hello"


def test_an_unreserved_order_waits_for_nothing(monkeypatch):
    env = session_env(monkeypatch)
    for reserved in (None, False, "true", 1):
        session = env.exec_mod.SecureLockSession(_app(env, reserved=reserved))
        assert session.reserved is False
        started = time.monotonic()
        assert session._fence_before_first_input() is True
        assert time.monotonic() - started < env.exec_mod.TICK_SECONDS
    assert env.exec_mod.SecureLockSession(_app(env, reserved=True)).reserved is True
