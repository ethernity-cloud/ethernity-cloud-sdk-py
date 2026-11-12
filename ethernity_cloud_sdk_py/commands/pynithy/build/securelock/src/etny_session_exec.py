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
                               input_cutoff_seconds, max_messages,
                               reserved}; reserved is true on a reserved
                              order (caller.securelock resolved to a
                              holder), absent otherwise
  session.fence.securelock    {order_id, bound, kind}, signed over
                              "<challenge>:<order_id>:session.fence:" +
                              plaintext (tests/spec/session-fence.json);
                              written before session.input.0 on a reserved
                              order, whose securelock waits for it for
                              READY_WINDOW_SECONDS and ends the task
                              without it; absent on an unreserved one,
                              which waits for nothing
  session.input.<seq>.securelock   one streamed input (+ .sig)
  session.output.<n>               {"ack", "code", "data", "commits",
                                    "esr": {"wallet", "commits"}}
                                   (tests/spec/session-commits.json)
  session.commit.<n>.<key16>.<relayNonce>.json   a step's signed commit
                                   authorization, staged with output n
  session.control.securelock       'close' or 'transfer'

A step's commits are held from the input's delivery to its output: the
output stages them and names them in its envelope, with the records and the
securelock's ESR wallet; the trustedzone takes esr.commits of every output
as the session's ledger. Authorizations still held when the session ends
with a result, on any path (the summary, a payload that could not set up,
a fence that never came, an executor failure), are staged as a plain
task's esr.commit.*.json. The 'transfer' control stops the step in hand,
the held authorizations are discarded and the session returns no result:
the process exits after the heartbeat's idle tail.

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

import ecld_state
import etny_exec
import securelock_probe
from etny_exec import TaskStatus

HANDLER_NAME = "___etny_on_input___"
HANDLER_GRACE_SECONDS = 30
TICK_SECONDS = 2
# How long a reserved order's session waits for session.fence.securelock
# before its first input (session-fence.json readyWindowSeconds); the task
# ends without it.
READY_WINDOW_SECONDS = 180
FENCE_OBJECT = "session.fence.securelock"
FENCE_KINDS = ("toBlock", "resultBlock", "none")
CONTROLS = ("close", "transfer")
# _handle's outcome when the transfer control stopped the step.
TRANSFER = "transfer"


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


def parse_fence_signal(text, order_id):
    """(kind, bound) of a fence plaintext: exactly the keys order_id, bound
    and kind in that order, order_id this order's as a number, bound a
    non-negative number, kind one of FENCE_KINDS, bound 0 exactly with kind
    none. Raises ValueError for anything else."""
    try:
        signal = json.loads(text)
    except ValueError:
        raise ValueError("session.fence is not JSON") from None
    if not isinstance(signal, dict) or list(signal) != ["order_id", "bound", "kind"]:
        raise ValueError("session.fence does not have exactly the keys order_id, bound, kind in that order")
    oid, bound, kind = signal["order_id"], signal["bound"], signal["kind"]
    if type(oid) is not int or oid != int(order_id):
        raise ValueError(f"session.fence order_id {oid!r} is not this order {int(order_id)}")
    if type(bound) is not int or bound < 0:
        raise ValueError(f"session.fence bound {bound!r} is not a non-negative integer")
    if kind not in FENCE_KINDS:
        raise ValueError(f"session.fence kind {kind!r} is not one of {', '.join(FENCE_KINDS)}")
    if (bound == 0) != (kind == "none"):
        raise ValueError(f"session.fence bound {bound} does not agree with kind {kind}")
    return kind, bound


class SecureLockSession:
    def __init__(self, app):
        self.app = app
        cfg = json.loads(app.get_and_verify_trustedzone_object("session.config.securelock"))
        self.order_id = int(cfg.get("order_id", -1))
        self.max_messages = int(cfg.get("max_messages", 256))
        self.reserved = cfg.get("reserved") is True
        started = time.monotonic()
        self.close_at = started + float(cfg.get("close_after_seconds", 0))
        self.cutoff_at = started + float(cfg.get("input_cutoff_seconds", 0))
        self.seen = 0
        self.processed = 0
        self.emitted = 0
        self.unprocessed = []
        self.reason = "RUNNING_PERIOD_COMPLETE"
        self.globals = etny_exec.session_base_globals()
        self.probe = getattr(app, "probe", None)
        self.esr = ecld_state.configured()

    # ------------------------------------------------------------------ pieces

    def _busy(self):
        if self.probe is not None:
            self.probe.busy(securelock_probe.PHASE_SESSION)

    def _idle(self):
        if self.probe is not None:
            self.probe.idle()

    def _present(self, name):
        status, _ = self.app.swift_stream_service.is_object_in_bucket(self.app.etny_bucket, name)
        return bool(status)

    def _initial_run(self):
        """Create the payload context. Its own result is not a session output
        -- setup code runs, a handler may get defined, state gets created.
        Its commits are held until the first output's envelope."""
        if self.esr:
            ecld_state.begin_step()
        self._busy()
        code, result = etny_exec.Exec(
            self.app.payload_data, self.app.input_data, globals=self.globals)
        self._idle()
        logging.info(f"session: payload context created (code {code})")
        if int(code) != int(TaskStatus.SUCCESS):
            # A payload that cannot even set up cannot serve a session.
            self.reason = "PAYLOAD_SETUP_FAILED"
            return code, result
        return None

    def _handle(self, seq, data):
        """Deliver one input; returns (code, output), None for a handler that
        hung past its budget, or TRANSFER when the transfer control arrived
        while the handler ran. The handler runs on a worker thread, joined in
        TICK_SECONDS slices with the control read between them, so a hung
        payload cannot outlive the timeout guard and a transfer stops the
        step. The worker itself cannot be interrupted: it is abandoned, the
        beats stay busy 1 until the probe's stop() writes the idle tail
        (busy 0, phase E on every exit path), and the trustedzone decides
        41 or 42 from the outputs it holds."""
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

        self._busy()
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        deadline = time.monotonic() + max(HANDLER_GRACE_SECONDS, self.close_at - time.monotonic())
        while True:
            worker.join(min(TICK_SECONDS, max(0.0, deadline - time.monotonic())))
            if not worker.is_alive():
                break
            if self._control_requested() == "transfer":
                logging.info(f"session: transfer control during input {seq}; stopping the step")
                return TRANSFER
            if time.monotonic() >= deadline:
                logging.error(f"session: handler hung on input {seq}; abandoning")
                self.unprocessed.append(seq)
                if self.esr:
                    ecld_state.discard_step_commits()
                return None
        return box.get("out", (int(TaskStatus.SYSTEM_ERROR), "SESSION HANDLER LOST"))

    def _session_binding(self, base_name):
        """The prefix each session object's signature covers ahead of the
        data: "<challenge>:<order_id>:<object>:", the task binding the
        trustedzone forwarded in task.securelock extended with the object's
        own name, so the trustedzone can refuse an object captured from
        another order, another run, or another sequence position. Empty
        without a binding (older trustedzone), which then verifies the bare
        data."""
        binding = getattr(self.app, "task_binding", None)
        if not binding:
            return ""
        return f"{binding['challenge']}:{binding['order_id']}:{base_name}:"

    def _emit(self, ack_seq, code, data):
        """Output n: the step's held commits staged as
        session.commit.<n>.<key16>.<relayNonce>.json, then the signed envelope
        naming them with their records and the ESR wallet (null and empty
        without a registry). A record whose callerUsed the payload forged
        makes the output a SECURITY_VIOLATION."""
        n = self.emitted
        names, records, tampered = ecld_state.stage_step_commits(
            n, getattr(self.app, "task_caller", None))
        if tampered:
            code = TaskStatus.SECURITY_VIOLATION
            data = ("SECURITY_VIOLATION: the task attempted to forge the state "
                    "commit caller. The attested submitter was re-asserted.")
        wallet = ecld_state.ledger_attestation()["wallet"] if self.esr else None
        envelope = json.dumps({
            "ack": ack_seq, "code": int(code), "data": data, "commits": names,
            "esr": {"wallet": wallet.lower() if wallet else None, "commits": records},
        }, separators=(",", ":"))
        name = f"session.output.{n}"
        self.app.encrypt_file_and_push_to_swifstream(
            envelope, name, signed_prefix=self._session_binding(name))
        self.emitted += 1

    def _control_requested(self):
        """The trustedzone-signed control, 'close' or 'transfer', or None.
        An unauthenticated object is ignored: the node could place one in the
        bucket, but only the trustedzone can SIGN one."""
        if not self._present("session.control.securelock"):
            return None
        try:
            control = str(self.app.get_and_verify_trustedzone_object("session.control.securelock")).strip()
        except Exception:
            return None
        return control if control in CONTROLS else None

    def _fence_before_first_input(self):
        """On a reserved order waits for the fence signal for
        READY_WINDOW_SECONDS, whatever else is staged; on an unreserved one
        waits for nothing. A signal present is verified under the fence's
        binding and the registry is fenced at its bound unless the kind is
        none. False when a reserved order's signal did not arrive in time."""
        deadline = time.monotonic() + READY_WINDOW_SECONDS
        while not self._present(FENCE_OBJECT):
            if not self.reserved:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(TICK_SECONDS)
        plaintext = self.app.get_and_verify_trustedzone_object(
            FENCE_OBJECT, signed_prefix=self._session_binding("session.fence"))
        kind, bound = parse_fence_signal(plaintext, self.order_id)
        logging.info(f"session: fence signal {kind} at {bound}")
        if kind != "none" and self.esr:
            fenced = ecld_state.StateRegistry().fence(bound)
            logging.info(f"session: fenced {len(fenced)} keys as of block {bound}")
        return True

    def _transfer(self):
        """The transfer control: the held authorizations are dropped and the
        session ends without a result."""
        if self.esr:
            ecld_state.discard_step_commits()
        self.reason = "TRANSFERRED"
        logging.info("session: service transferred; no result")
        return None

    # --------------------------------------------------------------------- run

    def run(self):
        """The session's result, None when the service was transferred. On
        every exit with a result the authorizations still held (a session
        that ends before any output, on whichever path) are staged as a
        plain task's esr.commit.*.json; a failure of the executor itself is
        the task's SYSTEM_ERROR, never a hang."""
        try:
            outcome = self._serve()
        except Exception as e:
            logging.error(f"session executor failed: {e}")
            outcome = (TaskStatus.SYSTEM_ERROR, f"SESSION EXECUTOR ERROR: {e}")
        if outcome is None:
            return None
        if self.esr and ecld_state.flush_held_commits(getattr(self.app, "task_caller", None)):
            return (TaskStatus.SECURITY_VIOLATION,
                    "SECURITY_VIOLATION: the task attempted to forge the state "
                    "commit caller. The attested submitter was re-asserted and "
                    "the task was terminated.")
        return outcome

    def _serve(self):
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
            self.app.encrypt_file_and_push_to_swifstream(
                ready, "session.ready",
                signed_prefix=self._session_binding("session.ready"))
        except Exception as e:
            logging.error(f"session: could not publish readiness: {e}")
        if not self._fence_before_first_input():
            self.reason = "FENCE_NOT_RECEIVED"
            logging.error(f"session: no fence signal within {READY_WINDOW_SECONDS} s; ending the task")
            return (TaskStatus.SYSTEM_ERROR,
                    f"SESSION FENCE ERROR: session.fence not staged within {READY_WINDOW_SECONDS} s")
        next_seq = 0
        while True:
            now = time.monotonic()
            if now >= self.close_at:
                self.reason = "RUNNING_PERIOD_COMPLETE"
                break
            control = self._control_requested()
            if control == "transfer":
                return self._transfer()
            if control == "close":
                self.reason = "CLOSED"
                break
            if next_seq >= self.max_messages:
                self.reason = "MESSAGE_CAP"
                break
            obj = f"session.input.{next_seq}.securelock"
            if not self._present(obj):
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
            if outcome is TRANSFER:
                return self._transfer()
            if outcome is not None:
                code, out = outcome
                # Only successful handling counts as processed; error outputs
                # (handler missing / handler raised) are still emitted+acked
                # so the dApp sees them, but the summary stays truthful.
                if int(code) == int(TaskStatus.SUCCESS):
                    self.processed += 1
                self._emit(next_seq, code, out)
                self._idle()
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
    """Entry point used by securelock.execute(). None when the service was
    transferred (no result is written); a failure before the session exists
    (session.config unreadable) or while staging the held authorizations
    is an ordinary task error, never a hang."""
    try:
        return SecureLockSession(app).run()
    except Exception as e:
        logging.error(f"session executor failed: {e}")
        return (TaskStatus.SYSTEM_ERROR, f"SESSION EXECUTOR ERROR: {e}")
