"""Interactive session executor for the securelock enclave.

Runs when the trustedzone staged 'session.config.securelock'. The payload
context is created once and kept alive; every streamed input is delivered
to the handler the payload registered:

    def ___etny_on_input___(data):
        ...
        return "reply"

A payload that defines no handler is not session-aware: each input is
answered with an explicit error output (no silent re-execution fallback),
so the dApp developer sees the problem on the first message.

Objects (all trustedzone<->securelock traffic uses the existing
encrypted+signed envelope in both directions):
  session.config.securelock   {order_id, close_after_seconds,
                               input_cutoff_seconds, max_messages}
  session.input.<seq>.securelock   one streamed input (+ .sig)
  session.output.<n>               {"ack": seq, "code": c, "data": out}
  session.control.securelock       'close'

The TIMEOUT GUARD is the point of this module: the running period ending is
a COMPLETION, not a failure. The guard arms from the attested
close_after_seconds delta (monotonic clock -- the enclave never trusts the
node's wall clock), stops pulling inputs at the cutoff, gives an in-flight
handler a short grace, and finalizes through the ordinary result path with
task code SUCCESS and a session summary, e.g.:

    {"session": "v1", "reason": "RUNNING_PERIOD_COMPLETE",
     "seen": 12, "processed": 12, "emitted": 12, "unprocessed": []}

so a full-duration session reads as 'securelock running period complete'.
The hard-kill path only remains for a guard that never returns -- a genuine
fault, refunded via the operator-fault flow.
"""

import json
import logging
import threading
import time

import etny_exec
from etny_exec import TaskStatus

HANDLER_NAME = "___etny_on_input___"
HANDLER_GRACE_SECONDS = 30
TICK_SECONDS = 2


def _render(value):
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _resolve_handler(base_globals):
    """The session input handler, from EITHER the namespaced `ecld.on_input`
    or the legacy bare `___etny_on_input___`. `ecld.on_input` wins if both are
    set; returns None if neither is a callable (handler-less payload)."""
    ecld = base_globals.get("ecld")
    if ecld is not None:
        h = getattr(ecld, "on_input", None)
        if callable(h):
            return h
    legacy = base_globals.get(HANDLER_NAME)
    return legacy if callable(legacy) else None


class SecureLockSession:
    def __init__(self, app):
        self.app = app
        cfg = json.loads(app.get_and_verify_trustedzone_object("session.config.securelock"))
        self.order_id = int(cfg.get("order_id", -1))
        self.max_messages = int(cfg.get("max_messages", 256))
        started = time.monotonic()
        self.close_at = started + float(cfg.get("close_after_seconds", 0))
        self.cutoff_at = started + float(cfg.get("input_cutoff_seconds", 0))
        self.seen = 0
        self.processed = 0
        self.emitted = 0
        self.unprocessed = []
        self.reason = "RUNNING_PERIOD_COMPLETE"
        self.globals = etny_exec.session_base_globals()

    # ------------------------------------------------------------------ pieces

    def _initial_run(self):
        """Create the payload context. Its own result is not a session output
        -- setup code runs, a handler may get defined, state gets created."""
        code, result = etny_exec.Exec(
            self.app.payload_data, self.app.input_data, globals=self.globals)
        logging.info(f"session: payload context created (code {code})")
        if int(code) != int(TaskStatus.SUCCESS):
            # A payload that cannot even set up cannot serve a session.
            self.reason = "PAYLOAD_SETUP_FAILED"
            return code, result
        return None

    def _handle(self, seq, data):
        """Deliver one input; returns (code, output). Runs the handler on a
        worker thread so a hung payload cannot outlive the timeout guard."""
        handler = _resolve_handler(self.globals)
        if not callable(handler):
            # No silent fallback: a payload without a handler is not
            # session-aware, so every input gets an explicit, acked error.
            return (int(TaskStatus.PAYLOAD_NOT_DEFINED),
                    "SESSION_HANDLER_NOT_DEFINED: this payload defines no "
                    "session input handler, so streamed inputs cannot be "
                    "processed. Set ecld.on_input = def handler(data): ... "
                    "(or the legacy ___etny_on_input___) in the payload and "
                    "republish.")
        box = {}

        def work():
            try:
                box["out"] = (int(TaskStatus.SUCCESS), _render(handler(data)))
            except Exception as e:
                box["out"] = (int(TaskStatus.SYSTEM_ERROR), f"SESSION HANDLER ERROR: {e}")

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        budget = max(HANDLER_GRACE_SECONDS, self.close_at - time.monotonic())
        worker.join(budget)
        if worker.is_alive():
            logging.error(f"session: handler hung on input {seq}; abandoning")
            self.unprocessed.append(seq)
            return None
        return box.get("out", (int(TaskStatus.SYSTEM_ERROR), "SESSION HANDLER LOST"))

    def _emit(self, ack_seq, code, data):
        envelope = json.dumps({"ack": ack_seq, "code": int(code), "data": data})
        self.app.encrypt_file_and_push_to_swifstream(
            envelope, f"session.output.{self.emitted}")
        self.emitted += 1

    def _close_requested(self):
        status, _ = self.app.swift_stream_service.is_object_in_bucket(
            self.app.etny_bucket, "session.control.securelock")
        if not status:
            return False
        # An unauthenticated close signal is ignored: the node could place an
        # object in the bucket, but only the trustedzone can SIGN one.
        try:
            control = self.app.get_and_verify_trustedzone_object("session.control.securelock")
            return str(control).strip() == "close"
        except Exception:
            return False

    # --------------------------------------------------------------------- run

    def run(self):
        setup_failure = self._initial_run()
        if setup_failure is not None:
            return setup_failure
        # READINESS HANDSHAKE: tell the trustedzone this securelock speaks the
        # session protocol AND whether the payload defined an input handler.
        # A pre-session securelock never writes this (the trustedzone answers
        # every input with an explicit error), and a handler-less payload is
        # announced by the trustedzone as a signed code-5 error row on the
        # DP-request metadata side before any input is even sent.
        try:
            ready = json.dumps({
                "ready": True,
                "handler": callable(_resolve_handler(self.globals)),
            })
            self.app.encrypt_file_and_push_to_swifstream(ready, "session.ready")
        except Exception as e:
            logging.error(f"session: could not publish readiness: {e}")
        next_seq = 0
        while True:
            now = time.monotonic()
            if now >= self.close_at:
                self.reason = "RUNNING_PERIOD_COMPLETE"
                break
            if self._close_requested():
                self.reason = "CLOSED"
                break
            if next_seq >= self.max_messages:
                self.reason = "MESSAGE_CAP"
                break
            obj = f"session.input.{next_seq}.securelock"
            status, _ = self.app.swift_stream_service.is_object_in_bucket(
                self.app.etny_bucket, obj)
            if not status:
                time.sleep(TICK_SECONDS)
                continue
            if now >= self.cutoff_at:
                # Delivered past the cutoff: the trustedzone answers these
                # with signed late notices; we only stop consuming.
                self.reason = "RUNNING_PERIOD_COMPLETE"
                break
            try:
                data = self.app.get_and_verify_trustedzone_object(obj)
            except Exception as e:
                logging.error(f"session: input {next_seq} failed verification: {e}")
                time.sleep(TICK_SECONDS)
                continue
            self.seen += 1
            outcome = self._handle(next_seq, data)
            if outcome is not None:
                code, out = outcome
                # Only successful handling counts as processed; error outputs
                # (handler missing / handler raised) are still emitted+acked
                # so the dApp sees them, but the summary stays truthful.
                if int(code) == int(TaskStatus.SUCCESS):
                    self.processed += 1
                self._emit(next_seq, code, out)
            next_seq += 1
        summary = {
            "session": "v1",
            "reason": self.reason,
            "seen": self.seen,
            "processed": self.processed,
            "emitted": self.emitted,
            "unprocessed": self.unprocessed,
        }
        logging.info(f"session: finalizing -- {summary}")
        return (TaskStatus.SUCCESS, json.dumps(summary))


def run_session(app):
    """Entry point used by securelock.execute(). Any unexpected failure
    degrades to an ordinary task error, never a hang."""
    try:
        return SecureLockSession(app).run()
    except Exception as e:
        logging.error(f"session executor failed: {e}")
        return (TaskStatus.SYSTEM_ERROR, f"SESSION EXECUTOR ERROR: {e}")
