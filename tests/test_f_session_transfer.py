"""The transfer control (PHASE-D-PLAN.md F.3) in etny_session_exec over
esr_local: a transfer staged mid-step stops the step (no session.output.<n>,
no session.commit.*, no esr objects, no result), the held records are
discarded and the relay-nonce memo rolled back, the beats stay busy (the
handler thread cannot be stopped) until the probe's stop() writes the idle
tail with busy 0 and phase E; a transfer between inputs does the same
with the boot commits; close after a finished step keeps the summary result
with the step's commit file (regression); close before any output stages
the boot commits as esr.commit.*.json (A25); a control failing signature
verification is ignored; a handler abandoned at its deadline leaves no held
commit."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from tests.f_session_support import BUCKET, CHALLENGE, ORDER_ID, FakeApp, content, decode_beat, load_modules, names, recording_swift

CALLER = "0x" + "11" * 20
PAYLOAD = """
from ecld_state import StateRegistry
def on_input(data):
    StateRegistry().commit("counter", lambda s: {**(s or {}), "n": (s or {}).get("n", 0) + 1})
    if data == "block":
        GATE.wait()
    return "done"
ecld.on_input = on_input
"""
BOOT_PAYLOAD = """
from ecld_state import StateRegistry
StateRegistry().commit("boot", lambda s: {"booted": True})
""" + PAYLOAD


@pytest.fixture
def env(monkeypatch):
    m = load_modules(monkeypatch)
    ecld_state, esr_local, exec_mod, probe_mod = m["ecld_state"], m["esr_local"], m["etny_session_exec"], m["securelock_probe"]
    monkeypatch.setattr(exec_mod, "TICK_SECONDS", 0.02)
    monkeypatch.setattr(probe_mod, "HEARTBEAT_PERIOD_SECONDS", 0.02)
    monkeypatch.setattr(probe_mod, "IDLE_TAIL_SECONDS", 0.04)
    swift = recording_swift(esr_local.MemSwift)
    reg = esr_local.MemRegistry()
    web3 = esr_local.MemWeb3(reg)
    ecld_state.configure(identity_priv=b"ecld-local-test-identity-key-0001", swift_stream_service=swift,
                         bucket=BUCKET, contract_address="0xLOCAL", web3=web3, caller=CALLER)
    ecld_state.set_local_commit_apply(
        lambda enclave, kh, cid, ev, nonce=0: reg.apply_commit(enclave, kh, cid, ev, nonce))
    gate = threading.Event()

    def start(payload=PAYLOAD, close_after=5):
        app = FakeApp(swift, caller=CALLER, payload=payload)
        app.stage("session.config.securelock", json.dumps(
            {"order_id": ORDER_ID, "close_after_seconds": close_after, "input_cutoff_seconds": close_after,
             "max_messages": 256}))
        app.probe = probe_mod.SecurelockProbe(app, CHALLENGE, ORDER_ID)
        app.probe.start()
        session = exec_mod.SecureLockSession(app)
        box = []
        thread = threading.Thread(target=lambda: box.append(session.run()), daemon=True)
        return SimpleNamespace(app=app, session=session, box=box, thread=thread)

    def wait_for(predicate, seconds=3.0):
        deadline = time.monotonic() + seconds
        while not predicate():
            assert time.monotonic() < deadline, "condition not met in time"
            time.sleep(0.005)

    yield SimpleNamespace(ecld_state=ecld_state, exec_mod=exec_mod, swift=swift, reg=reg, gate=gate,
                         start=start, wait_for=wait_for)
    gate.set()


def _run(env, run, after_setup=None):
    """Runs the session on its thread; once the payload context exists the
    handler's gate is injected, then `after_setup` stages the inputs and the
    control; returns run()'s result."""
    run.thread.start()
    env.wait_for(lambda: "on_input" in run.session.globals or run.box)
    run.session.globals["GATE"] = env.gate
    if after_setup:
        after_setup()
    run.thread.join(5)
    assert not run.thread.is_alive()
    return run.box[0]


def test_a_transfer_mid_step_stops_the_step_and_discards_its_commits(env):
    run = env.start()

    def transfer_once_the_step_committed():
        run.app.stage("session.input.0.securelock", "block")
        env.wait_for(lambda: len(env.ecld_state._held or []) == 1)
        assert env.ecld_state._task_relay_nonces != {}
        run.app.stage("session.control.securelock", "transfer")

    result = _run(env, run, transfer_once_the_step_committed)
    assert result is None and run.session.reason == "TRANSFERRED"
    assert names(env.swift, "session.output.") == [] and names(env.swift, "session.commit.") == []
    assert names(env.swift, "esr.") == [] and names(env.swift, "result") == []
    assert env.ecld_state._held == [] and env.ecld_state._task_relay_nonces == {}
    assert env.ecld_state._task_writes == {} and env.reg.entry_count() == 0
    assert run.app.probe.state()[1:3] == (1, 2)
    # The abandoned handler keeps the beats busy until the tail: stop() beats
    # busy 0 and phase E, the trustedzone deciding 41 or 42 from its outputs.
    before = len(env.swift.beats)
    run.app.probe.stop()
    tail = [decode_beat(b, run.app.probe._key, run.app.probe._aad) for b in env.swift.beats[before:]]
    assert len(tail) == 2 and all((b["busy"], b["runs"], b["phase"]) == (0, 2, "E") for b in tail)


def test_a_transfer_between_inputs_discards_the_boot_commits(env):
    run = env.start(BOOT_PAYLOAD)
    run.app.stage("session.control.securelock", "transfer")
    result = _run(env, run)
    assert result is None
    assert env.ecld_state._held == [] and names(env.swift, "esr.") == []
    assert names(env.swift, "session.output.") == [] and run.app.probe.state()[1:3] == (0, 1)


def test_close_after_a_finished_step_keeps_the_summary_result(env):
    run = env.start()

    def close_once_answered():
        run.app.stage("session.input.0.securelock", "go")
        env.wait_for(lambda: "session.output.0" in run.app.pushed_names())
        run.app.stage("session.control.securelock", "close")

    code, result = _run(env, run, close_once_answered)
    summary = json.loads(result)
    assert (code, summary["reason"], summary["emitted"], summary["processed"]) == (0, "CLOSED", 1, 1)
    envelope = json.loads(run.app.pushed_data("session.output.0"))
    assert envelope["ack"] == 0 and len(envelope["commits"]) == 1
    assert envelope["commits"][0].startswith("session.commit.0.") and envelope["commits"][0].endswith(".0.json")
    assert names(env.swift, "session.commit.") == envelope["commits"]
    assert envelope["esr"]["wallet"] == env.ecld_state.StateRegistry().wallet_address.lower()
    assert json.loads(content(env.swift, "esr.authorizations.json")) == envelope["esr"]["commits"]
    assert names(env.swift, "esr.commit.") == [] and env.reg.entry_count() == 1
    assert run.app.probe.state()[1:3] == (0, 2)


def test_close_before_any_output_stages_the_boot_commits_as_esr_commit_files(env):
    run = env.start(BOOT_PAYLOAD)
    run.app.stage("session.control.securelock", "close")
    code, result = _run(env, run)
    assert (code, json.loads(result)["reason"]) == (0, "CLOSED")
    files = names(env.swift, "esr.commit.")
    assert len(files) == 1 and files[0].endswith(".0.json") and names(env.swift, "session.commit.") == []
    assert json.loads(content(env.swift, "esr.authorizations.json")) == [json.loads(content(env.swift, files[0]))]
    assert env.ecld_state._held is None and env.reg.entry_count() == 1


def test_a_control_failing_verification_is_ignored(env):
    run = env.start(close_after=0.5)
    run.app.stage("session.control.securelock", "transfer", bad_signature=True)
    code, result = _run(env, run, lambda: run.app.stage("session.input.0.securelock", "go"))
    assert (code, json.loads(result)["reason"]) == (0, "RUNNING_PERIOD_COMPLETE")
    assert "session.output.0" in run.app.pushed_names()


def test_a_handler_abandoned_at_its_deadline_leaves_no_held_commit(env, monkeypatch):
    monkeypatch.setattr(env.exec_mod, "HANDLER_GRACE_SECONDS", 0.1)
    run = env.start(close_after=0.3)
    code, result = _run(env, run, lambda: run.app.stage("session.input.0.securelock", "block"))
    summary = json.loads(result)
    assert (code, summary["unprocessed"], summary["emitted"]) == (0, [0], 0)
    assert names(env.swift, "session.commit.") == [] and names(env.swift, "esr.") == []
    assert env.ecld_state._task_relay_nonces == {}


def test_the_fence_plaintext_is_parsed_strictly(env):
    parse = env.exec_mod.parse_fence_signal
    assert parse('{"order_id":4101,"bound":52646961,"kind":"toBlock"}', 4101) == ("toBlock", 52646961)
    assert parse('{"order_id":4101,"bound":0,"kind":"none"}', 4101) == ("none", 0)
    for bad in ('{"order_id":4101,"bound":5,"kind":"none"}', '{"order_id":4101,"bound":0,"kind":"toBlock"}',
                '{"bound":5,"order_id":4101,"kind":"toBlock"}', '{"order_id":4100,"bound":5,"kind":"toBlock"}',
                '{"order_id":"4101","bound":5,"kind":"toBlock"}', '{"order_id":4101,"bound":"5","kind":"toBlock"}',
                '{"order_id":4101,"bound":5,"kind":"later"}', '{"order_id":4101,"bound":5,"kind":"toBlock","x":1}',
                '[]', 'nope'):
        with pytest.raises(ValueError):
            parse(bad, 4101)
