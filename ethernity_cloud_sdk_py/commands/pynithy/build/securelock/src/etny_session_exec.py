"""Interactive session executor for the securelock enclave.

Runs when the trustedzone staged 'session.config.securelock'. The payload
context is created once and kept alive; every streamed input is delivered
either to a handler the payload registered:

    def ___etny_on_input___(data):
        ...
        return "reply"

or -- when no handler is defined -- by re-executing the payload against the
SAME globals with ___etny_data_set___ bound to the new input, so module-level
state persists across messages.

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
        handler = self.globals.get(HANDLER_NAME)
        box = {}

        def work():
            try:
                if callable(handler):
                    box["out"] = (int(TaskStatus.SUCCESS), _render(handler(data)))
                else:
                    code, result = etny_exec.Exec(
                        self.app.payload_data, data, globals=self.globals)
                    box["out"] = (int(code), _render(result))
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
        return bool(status)

    # --------------------------------------------------------------------- run

    def run(self):
        setup_failure = self._initial_run()
        if setup_failure is not None:
            return setup_failure
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
