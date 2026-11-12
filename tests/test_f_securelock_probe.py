"""securelock_probe (PHASE-D-PLAN.md F.1) against tests/spec/heartbeat.json:
the two beats reproduced from key, seq, record and AAD, the 60-byte body and
80-character base64, the nonce equal to seq, the securelock.ready plaintext
and its signed binding; over a recording MemSwift: the ready object (and its
signature) written before any beat, one beat per period, busy 1 and runs
moved by the payload edge, the idle tail (busy 0, phase E) written by stop()
on every exit path, a tick due while a put is in flight skipped and never
queued, a refused heartbeat put logged, a refused ready put raised."""
import base64
import json
import logging
import time

import pytest

from tests.f_session_support import CHALLENGE, ORDER_ID, SPEC, FakeApp, decode_beat as decode, recording_swift, load_modules

HB = json.loads((SPEC / "heartbeat.json").read_text(encoding="utf-8"))
KEY = bytes.fromhex(HB["key"]["hex"])
AAD = HB["aad"]["value"]
PERIOD = 0.02
TAIL = 0.1


@pytest.fixture
def mods(monkeypatch):
    modules = load_modules(monkeypatch)
    probe = modules["securelock_probe"]
    monkeypatch.setattr(probe, "HEARTBEAT_PERIOD_SECONDS", PERIOD)
    monkeypatch.setattr(probe, "IDLE_TAIL_SECONDS", TAIL)
    return modules


def wait_for(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.005)


def test_the_fixture_beats_are_reproduced_from_key_seq_record_and_aad(mods):
    probe = mods["securelock_probe"]
    for name in ("idle", "busy"):
        beat = HB["beats"][name]
        body = probe.encode_beat(KEY, beat["seq"], beat["busy"], beat["runs"], beat["phase"], AAD)
        assert body == beat["bodyBase64"] and len(body) == 80
        raw = base64.b64decode(body)
        assert len(raw) == beat["bodyBytes"] == 60
        assert raw[:12].hex() == beat["nonceHex"][2:]
        assert raw[12:44].hex() == beat["ciphertextHex"][2:] and raw[44:].hex() == beat["tagHex"][2:]
        assert probe.record(beat["seq"], beat["busy"], beat["runs"], beat["phase"]) == beat["plaintext"].encode()
        assert decode(body, KEY, AAD)["text"] == beat["plaintext"]
    assert probe.heartbeat_aad(CHALLENGE, ORDER_ID) == AAD
    with pytest.raises(ValueError):
        probe.record(1, 0, 10 ** 6, "T")


def test_the_ready_plaintext_and_its_binding(mods):
    probe = mods["securelock_probe"]
    assert probe.ready_plaintext(ORDER_ID, KEY) == HB["ready"]["plaintext"]
    assert json.loads(probe.ready_plaintext(ORDER_ID, KEY)) == HB["ready"]["fields"]
    assert probe.ready_binding(CHALLENGE, ORDER_ID) == f"{CHALLENGE}:{ORDER_ID}:securelock.ready:"
    assert probe.SEQ0 == HB["ready"]["fields"]["seq0"]


def test_next_tick_skips_the_ticks_a_put_overran(mods, monkeypatch):
    probe = mods["securelock_probe"]
    monkeypatch.setattr(probe, "HEARTBEAT_PERIOD_SECONDS", 1.0)
    assert probe.next_tick(10.0, 10.5) == 11.0
    assert probe.next_tick(10.0, 11.0) == 12.0
    assert probe.next_tick(10.0, 12.3) == 13.0


def test_the_ready_object_precedes_the_beats_which_follow_the_payload_edges(mods):
    probe_mod, esr_local = mods["securelock_probe"], mods["esr_local"]
    swift = recording_swift(esr_local.MemSwift)
    app = FakeApp(swift)
    probe = probe_mod.SecurelockProbe(app, CHALLENGE, ORDER_ID)
    probe.start()
    key = probe._key
    assert app.pushed[0] == ("securelock.ready", probe_mod.ready_plaintext(ORDER_ID, key),
                             f"{CHALLENGE}:{ORDER_ID}:securelock.ready:")
    assert list(json.loads(app.pushed[0][1])) == ["order_id", "key", "seq0"]
    assert "ecld-local/securelock.ready.sig" in swift._store
    wait_for(lambda: len(swift.beats) >= 3)
    first = [decode(b, key, AAD) for b in swift.beats[:3]]
    assert [b["seq"] for b in first] == [1, 2, 3]
    assert all((b["busy"], b["runs"], b["phase"]) == (0, 0, "B") for b in first)
    probe.busy("T")
    wait_for(lambda: decode(swift.beats[-1], key, AAD)["busy"] == 1)
    assert decode(swift.beats[-1], key, AAD)["runs"] == 1
    assert decode(swift.beats[-1], key, AAD)["phase"] == "T"
    probe.idle()
    wait_for(lambda: decode(swift.beats[-1], key, AAD)["busy"] == 0)
    assert decode(swift.beats[-1], key, AAD)["runs"] == 1
    probe.busy("S")
    wait_for(lambda: decode(swift.beats[-1], key, AAD)["runs"] == 2)
    before = len(swift.beats)
    started = time.monotonic()
    probe.stop()
    elapsed = time.monotonic() - started
    tail = [decode(b, key, AAD) for b in swift.beats[before:]]
    assert len(tail) == int(TAIL / PERIOD) == 5
    assert all((b["busy"], b["phase"]) == (0, "E") for b in tail)
    assert elapsed >= TAIL * 0.9
    seqs = [decode(b, key, AAD)["seq"] for b in swift.beats]
    assert seqs == list(range(1, len(seqs) + 1))
    count = len(swift.beats)
    probe.stop()
    time.sleep(3 * PERIOD)
    assert len(swift.beats) == count


def test_a_tick_due_while_a_put_is_in_flight_is_skipped_never_queued(mods):
    probe_mod, esr_local = mods["securelock_probe"], mods["esr_local"]
    swift = recording_swift(esr_local.MemSwift, put_delay=3 * PERIOD)
    app = FakeApp(swift)
    probe = probe_mod.SecurelockProbe(app, CHALLENGE, ORDER_ID)
    probe.start()
    time.sleep(15 * PERIOD)
    probe._stop.set()
    probe._thread.join()
    seqs = [decode(b, probe._key, AAD)["seq"] for b in swift.beats]
    assert seqs == list(range(1, len(seqs) + 1))
    assert 2 <= len(seqs) <= 15 // 3 + 2


def test_a_refused_heartbeat_put_is_logged_and_a_refused_ready_put_raises(mods, caplog):
    probe_mod, esr_local = mods["securelock_probe"], mods["esr_local"]
    swift = recording_swift(esr_local.MemSwift, refuse=("securelock.heartbeat",))
    probe = probe_mod.SecurelockProbe(FakeApp(swift), CHALLENGE, ORDER_ID)
    with caplog.at_level(logging.ERROR):
        assert probe.beat() == 1
    assert "heartbeat 1 not written" in caplog.text
    refused = recording_swift(esr_local.MemSwift, refuse=("securelock.ready",))
    with pytest.raises(RuntimeError, match="securelock.ready refused"):
        probe_mod.SecurelockProbe(FakeApp(refused), CHALLENGE, ORDER_ID).start()
