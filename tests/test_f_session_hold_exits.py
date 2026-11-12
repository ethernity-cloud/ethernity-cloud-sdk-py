"""The step hold on the session's exits (PHASE-D-PLAN.md A25, F.2): the
boot commits held by the payload's initial run are staged as a plain
task's esr.commit.<key16>.<relayNonce>.json, with the ledger rewritten,
when the session ends with a result before any output on every path (a
payload that could not set up, a fence signal failing verification, the
fence window's expiry), the hold closed; discard_step_commits restores
_task_ledger with the other memos, so ledger_snapshot reports no state of
a discarded commit."""
import json

from tests.f_session_support import CALLER, FakeApp, ORDER_ID, content, names, session_env

BOOT = 'from ecld_state import StateRegistry\nStateRegistry().commit("boot", lambda s: {"booted": True})\n'
HANDLER = 'ecld.on_input = lambda data: "ok"\n'


def _app(env, payload, reserved=False):
    app = FakeApp(env.swift, caller=CALLER, payload=payload)
    app.stage("session.config.securelock", json.dumps(
        {"order_id": ORDER_ID, "close_after_seconds": 5, "input_cutoff_seconds": 5, "max_messages": 256,
         "reserved": reserved}))
    return app


def _staged_boot_commit(env):
    files = names(env.swift, "esr.commit.")
    assert len(files) == 1 and files[0].endswith(".0.json") and names(env.swift, "session.commit.") == []
    record = json.loads(content(env.swift, files[0]))
    assert record["callerUsed"] == CALLER
    assert json.loads(content(env.swift, "esr.authorizations.json")) == [record]
    assert env.ecld_state.ledger_attestation()["commits"] == [record]
    assert env.ecld_state._held is None and env.reg.entry_count() == 1


def test_a_payload_that_cannot_set_up_leaves_its_boot_commits_staged(monkeypatch):
    env = session_env(monkeypatch)
    session = env.exec_mod.SecureLockSession(_app(env, BOOT + 'raise RuntimeError("no handler today")\n'))
    code, result = session.run()
    assert int(code) == int(env.exec_mod.TaskStatus.BASE_EXCEPTION) and "no handler today" in result
    assert session.reason == "PAYLOAD_SETUP_FAILED"
    _staged_boot_commit(env)


def test_a_fence_failing_verification_ends_the_executor_with_the_boot_commits_staged(monkeypatch):
    env = session_env(monkeypatch)
    app = _app(env, BOOT + HANDLER, reserved=True)
    app.stage("session.fence.securelock", '{"order_id":4101,"bound":7,"kind":"toBlock"}', bad_signature=True)
    code, result = env.exec_mod.SecureLockSession(app).run()
    assert int(code) == int(env.exec_mod.TaskStatus.SYSTEM_ERROR)
    assert result == "SESSION EXECUTOR ERROR: trustedzone signature invalid for session.fence.securelock"
    _staged_boot_commit(env)


def test_the_fence_windows_expiry_leaves_the_boot_commits_staged(monkeypatch):
    env = session_env(monkeypatch)
    monkeypatch.setattr(env.exec_mod, "READY_WINDOW_SECONDS", 0.05)
    code, result = env.exec_mod.SecureLockSession(_app(env, BOOT + HANDLER, reserved=True)).run()
    assert int(code) == int(env.exec_mod.TaskStatus.SYSTEM_ERROR) and result.startswith("SESSION FENCE ERROR")
    _staged_boot_commit(env)


def test_discard_step_commits_restores_the_task_ledger(monkeypatch):
    env = session_env(monkeypatch)
    ecld_state = env.ecld_state
    registry = ecld_state.StateRegistry()
    registry.commit("kept", lambda s: {"v": 1})
    kept = dict(ecld_state._task_ledger)
    ecld_state.begin_step()
    registry.commit("dropped", lambda s: {"v": 2})
    assert set(ecld_state._task_ledger) == {"kept", "dropped"}
    ecld_state.discard_step_commits()
    assert ecld_state._task_ledger == kept
    assert [e["key"] for e in ecld_state.ledger_snapshot()["entries"]] == ["kept"]
    assert ecld_state._held == [] and "dropped" not in ecld_state._task_writes
