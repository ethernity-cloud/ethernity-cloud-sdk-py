"""The securelock's ready object and heartbeat (tests/spec/heartbeat.json).

At boot the securelock draws a 32-byte key and announces it in
securelock.ready, the compact JSON {"order_id": <n>, "key": <64 hex>,
"seq0": 1}, encrypted for the trustedzone and signed over
"<challenge>:<order_id>:securelock.ready:" + plaintext. A daemon thread then
overwrites securelock.heartbeat once per HEARTBEAT_PERIOD_SECONDS with

  base64(nonce (12) || AES-256-GCM(key, nonce, record, aad) || tag (16))

80 characters for the 32-byte record

  ecld-hb|v1|<seq 10 digits>|<busy 1 digit>|<runs 6 digits>|<phase 1 char>

the nonce seq as a big-endian integer in its low-order 8 bytes, the AAD
"<challenge>:<order_id>:securelock.heartbeat". seq starts at seq0 and grows
by one per beat; a tick that falls due while a put is in flight is skipped,
never queued. busy is 1 from the start of a payload run to its output and
runs counts the starts; the phase is B before the payload, T a task payload,
S a session and E the idle tail, the IDLE_TAIL_SECONDS of beats with busy 0
that stop() writes on its caller's thread before the process exits.
"""

import base64
import io
import json
import logging
import secrets
import threading
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HEARTBEAT_OBJECT = "securelock.heartbeat"
READY_OBJECT = "securelock.ready"
HEARTBEAT_PERIOD_SECONDS = 1.0
IDLE_TAIL_SECONDS = 10
KEY_BYTES = 32
NONCE_BYTES = 12
PLAINTEXT_BYTES = 32
SEQ0 = 1
PHASE_BOOT = "B"
PHASE_TASK = "T"
PHASE_SESSION = "S"
PHASE_TAIL = "E"


def heartbeat_aad(challenge, order_id):
    """The AAD of every beat of an order."""
    return f"{challenge}:{int(order_id)}:{HEARTBEAT_OBJECT}"


def ready_binding(challenge, order_id):
    """The prefix securelock.ready is signed with, ahead of its plaintext."""
    return f"{challenge}:{int(order_id)}:{READY_OBJECT}:"


def ready_plaintext(order_id, key):
    """The securelock.ready plaintext: the keys order_id, key, seq0 in that
    order, no whitespace."""
    return json.dumps({"order_id": int(order_id), "key": key.hex(), "seq0": SEQ0},
                      separators=(",", ":"))


def record(seq, busy, runs, phase):
    """The 32-byte fixed-width heartbeat record."""
    data = f"ecld-hb|v1|{int(seq):010d}|{int(busy):d}|{int(runs):06d}|{phase}".encode("ascii")
    if len(data) != PLAINTEXT_BYTES:
        raise ValueError(f"heartbeat record is {len(data)} bytes, not {PLAINTEXT_BYTES}")
    return data


def encode_beat(key, seq, busy, runs, phase, aad):
    """One beat's body, base64: the nonce, the ciphertext of the record and
    the GCM tag under `key` and `aad`."""
    nonce = int(seq).to_bytes(NONCE_BYTES, "big")
    body = nonce + AESGCM(key).encrypt(nonce, record(seq, busy, runs, phase), aad.encode("utf-8"))
    return base64.b64encode(body).decode("ascii")


def next_tick(previous, now):
    """The first tick after `previous` that is still ahead of `now`: the
    ticks that fell due while the last put was in flight are skipped."""
    tick = previous + HEARTBEAT_PERIOD_SECONDS
    while tick <= now:
        tick += HEARTBEAT_PERIOD_SECONDS
    return tick


class SecurelockProbe:
    """One order's ready object and heartbeat. `app` is the securelock:
    encrypt_file_and_push_to_swifstream writes the signed, encrypted ready
    object; swift_stream_service and etny_bucket take the beats."""

    def __init__(self, app, challenge, order_id):
        self._app = app
        self._order_id = int(order_id)
        self._key = secrets.token_bytes(KEY_BYTES)
        self._aad = heartbeat_aad(challenge, order_id)
        self._binding = ready_binding(challenge, order_id)
        self._lock = threading.Lock()
        self._seq = SEQ0
        self._busy = 0
        self._runs = 0
        self._phase = PHASE_BOOT
        self._stop = threading.Event()
        self._thread = None
        self._stopped = False

    def start(self):
        """Writes securelock.ready, then starts the beat thread. A refused
        put raises from encrypt_file_and_push_to_swifstream."""
        self._app.encrypt_file_and_push_to_swifstream(
            ready_plaintext(self._order_id, self._key), READY_OBJECT,
            signed_prefix=self._binding)
        self._thread = threading.Thread(target=self._run, name="securelock-heartbeat", daemon=True)
        self._thread.start()

    def busy(self, phase):
        """A payload run starts: busy 1, runs + 1, the given phase."""
        with self._lock:
            self._busy = 1
            self._runs += 1
            self._phase = phase

    def idle(self):
        """The payload run's output is out: busy 0."""
        with self._lock:
            self._busy = 0

    def phase(self, phase):
        with self._lock:
            self._phase = phase

    def state(self):
        """(seq of the next beat, busy, runs, phase)."""
        with self._lock:
            return self._seq, self._busy, self._runs, self._phase

    def beat(self):
        """Writes the next beat and returns its seq. A refused put is logged:
        a beat the trustedzone does not see is a stale heartbeat on its side,
        and the task's own puts report the bucket's state."""
        with self._lock:
            seq = self._seq
            body = encode_beat(self._key, seq, self._busy, self._runs, self._phase, self._aad)
            self._seq += 1
        ok, message = self._app.swift_stream_service.put_file_content(
            self._app.etny_bucket, HEARTBEAT_OBJECT, "", io.BytesIO(body.encode("ascii")))
        if not ok:
            logging.error(f"heartbeat {seq} not written: {message}")
        return seq

    def _run(self):
        tick = time.monotonic()
        while not self._stop.is_set():
            self.beat()
            tick = next_tick(tick, time.monotonic())
            delay = tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def stop(self):
        """Ends the beat thread, then writes the idle tail on the calling
        thread: IDLE_TAIL_SECONDS / HEARTBEAT_PERIOD_SECONDS beats with busy
        0 and phase E, one per period. A second call returns at once."""
        if self._stopped:
            return
        self._stopped = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            self._busy = 0
            self._phase = PHASE_TAIL
        for _ in range(int(IDLE_TAIL_SECONDS / HEARTBEAT_PERIOD_SECONDS)):
            self.beat()
            time.sleep(HEARTBEAT_PERIOD_SECONDS)
