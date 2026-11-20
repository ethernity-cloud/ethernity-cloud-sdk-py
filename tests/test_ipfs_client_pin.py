"""A pin on an IPFS API is a `pin/add` asked with progress=true and without
compression under the client's Authorization header and read as a stream of
lines, a byte at a read, true when a line names the CID among its Pins. An
attempt that reads no such line is followed by `pin/ls`, and by another
`pin/add` when the API does not hold the pin, at most PIN_ATTEMPTS times and
never after the pin's time; a 401 or 403 ends the pin at once. No network:
requests.post and the module's clock are replaced, and the cases over HTTP
run against a server on the loopback interface."""
import http.server
import threading
import time

import pytest
import requests

from ethernity_cloud_sdk_py.commands.pynithy import ipfs_client as module
from ethernity_cloud_sdk_py.commands.pynithy.ipfs_client import IPFSClient
from ethernity_cloud_sdk_py.commands.spinner import Spinner

API = "https://ipfs.example"
CID = "QmbohHhXrfkpnzg7uGQ1LHRA1jXD6K12PWTkpiRGXUmPa1"
OTHER_CID = "QmWVrXJYqXbgWNmtZAPqjnXai1NASKQumgwB3XytD3JEvf"
TOKEN = {"Authorization": "writer-token"}
# The headers of a `pin/add`: the token, and an answer that is not compressed.
UNCOMPRESSED = {**TOKEN, "Accept-Encoding": "identity"}
PIN_ADD_PATH = f"/api/v0/pin/add?arg={CID}&recursive=true&progress=true"
PIN_LS_PATH = f"/api/v0/pin/ls?arg={CID}&type=recursive"
PIN_ADD = API + PIN_ADD_PATH
PIN_LS = API + PIN_LS_PATH
# The timeouts of a `pin/add` (to connect, between two chunks) and of a
# `pin/ls`, with the pin's hour ahead.
STREAMED = (30, 120)
ASKED = 30
# Progress lines as Kubo 0.4.23 writes them (a null Pins) and as later
# versions do (empty fields left out, so no field at all on a tick before the
# first block; Bytes beside Progress), and a blank line.
PROGRESS = (b'{"Pins":null,"Progress":3}', b"{}", b"", b'{"Progress":41,"Bytes":10485760}')
PINNED = b'{"Pins":["%s"]}' % CID.encode()
# haproxy's answer for a backend that is down.
UNAVAILABLE = "<html><body><h1>503 Service Unavailable</h1>\nNo server is available to handle this request.\n</body></html>\n"
FAILED = f"\tFailed to pin {CID} on {API}"
SPINNER_LINE = f"Pinning {CID} on the public IPFS API"


class _Answer:
    """What requests.post returns: a status and a body, which `text` holds
    whole and a read of the stream returns in one piece."""

    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.text.encode()

    def close(self):
        self.closed = True


class _Stream(_Answer):
    """An answer read as a stream, a 200 unless `status_code` is given. Each
    of `pieces`, with `end` after it, is what one read returns and takes
    `seconds` of the clock to arrive; an exception among the pieces is raised
    where the reading reaches it."""

    def __init__(self, *pieces, seconds=0, end=b"\n", status_code=200):
        super().__init__(status_code, "")
        self.pieces = pieces
        self.seconds = seconds
        self.end = end
        self.read = 0

    def iter_content(self, chunk_size):
        for piece in self.pieces:
            self.clock.now += self.seconds
            if isinstance(piece, Exception):
                raise piece
            self.read += 1
            yield piece + self.end


def _listed():
    """`pin/ls` of a CID the API holds under a recursive pin."""
    return _Answer(200, '{"Keys":{"%s":{"Type":"recursive"}}}\n' % CID)


def _not_pinned():
    """`pin/ls` of a CID the API holds no recursive pin of."""
    return _Answer(500, '{"Message":"path \'%s\' is not pinned","Code":0,"Type":"error"}\n' % CID)


class _Api:
    """requests.post and the module's clock replaced. A request is recorded
    as (url, headers, stream, timeout), with the clock's reading in `times`,
    and answered with the next of `answers`, an exception among them raised.
    The clock starts at 0 and moves only as `sleep` and the pieces of a
    _Stream move it."""

    def __init__(self, monkeypatch, *answers):
        self.answers = list(answers)
        self.calls, self.times, self.sleeps = [], [], []
        self.now = 0
        monkeypatch.setattr(module.requests, "post", self.post)
        monkeypatch.setattr(module, "time", self)

    @property
    def urls(self):
        return [call[0] for call in self.calls]

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def post(self, url, headers=None, timeout=None, stream=False):
        assert self.answers, f"a request the test holds no answer for: {url}"
        self.calls.append((url, headers, stream, timeout))
        self.times.append(self.now)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        answer.clock = self
        return answer


def _each_followed_by_not_pinned(attempts):
    return [answer for attempt in attempts for answer in (attempt, _not_pinned())]


def _pin(**kwargs):
    return IPFSClient(API, "writer-token").pin(CID, **kwargs)


def test_the_bounds_of_a_pin():
    assert (module.PIN_ATTEMPTS, module.PIN_RETRY_SECONDS) == (5, 10)
    assert (module.PIN_REQUEST_SECONDS, module.PIN_SILENCE_SECONDS) == (30, 120)
    assert module.PIN_READ_BYTES == 1


def test_a_pin_is_a_recursive_pin_add_with_progress_the_token_and_no_compression(monkeypatch):
    stream = _Stream(PINNED)
    api = _Api(monkeypatch, stream)
    assert _pin() is True
    assert api.calls == [(PIN_ADD, UNCOMPRESSED, True, STREAMED)]
    assert stream.closed


def test_a_pin_add_with_less_time_than_its_bounds_has_both_cut(monkeypatch):
    api = _Api(monkeypatch, _Stream(PINNED))
    assert _pin(timeout_secs=20) is True
    assert api.calls == [(PIN_ADD, UNCOMPRESSED, True, (20, 20))]


def test_progress_lines_then_the_pin_line_is_true(monkeypatch, capsys):
    stream = _Stream(*PROGRESS, PINNED)
    api = _Api(monkeypatch, stream)
    assert _pin() is True
    assert api.urls == [PIN_ADD]
    assert (stream.read, stream.closed) == (len(PROGRESS) + 1, True)
    assert capsys.readouterr().out == ""


def test_a_pin_line_at_once_is_true(monkeypatch, capsys):
    """An answer of the pin line alone, as for a CID the API pinned before."""
    api = _Api(monkeypatch, _Stream(PINNED))
    assert _pin() is True
    assert api.urls == [PIN_ADD]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("pieces", [
    (b'{"Pins":nu', b'll,"Progress":3}\n{"Progress":41}\n{"Pi', b'ns":["%s"]}\n' % CID.encode()),
    tuple(bytes([byte]) for byte in PROGRESS[0] + b"\n" + PINNED + b"\n"),
], ids=["lines split and joined", "a byte each"])
def test_the_lines_are_put_together_from_the_pieces_the_reads_return(monkeypatch, capsys, pieces):
    stream = _Stream(*pieces, end=b"")
    api = _Api(monkeypatch, stream)
    assert _pin() is True
    assert api.urls == [PIN_ADD]
    assert stream.read == len(pieces)
    assert capsys.readouterr().out == ""


def test_a_last_line_without_a_line_feed_is_read(monkeypatch, capsys):
    api = _Api(monkeypatch, _Stream(PROGRESS[0] + b"\n" + PINNED, end=b""))
    assert _pin() is True
    assert api.urls == [PIN_ADD]
    assert capsys.readouterr().out == ""


def test_an_answer_that_ends_without_the_pin_line_is_settled_by_pin_ls(monkeypatch, capsys):
    api = _Api(monkeypatch, _Stream(*PROGRESS), _listed())
    assert _pin() is True
    assert api.calls == [(PIN_ADD, UNCOMPRESSED, True, STREAMED), (PIN_LS, TOKEN, False, ASKED)]
    assert api.sleeps == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("unlisted", [
    _not_pinned,
    lambda: _Answer(200, '{"Keys":{"%s":{"Type":"recursive"}}}\n' % OTHER_CID),
    lambda: _Answer(500, '{"Keys":{"%s":{"Type":"recursive"}}}\n' % CID),
    lambda: _Answer(200, '{"Keys":"x%sy"}\n' % CID),
    lambda: _Answer(200, '{"Keys":7}\n'),
    lambda: _Answer(503, UNAVAILABLE),
    lambda: requests.exceptions.ConnectionError("Connection refused"),
], ids=["not pinned", "another CID listed", "the CID listed under a 500", "Keys a string that holds the CID",
        "Keys a number", "a 503", "no answer"])
def test_a_pin_the_api_does_not_list_is_asked_again(monkeypatch, capsys, unlisted):
    api = _Api(monkeypatch, _Stream(*PROGRESS), unlisted(), _Stream(*PROGRESS, PINNED))
    assert _pin() is True
    assert api.urls == [PIN_ADD, PIN_LS, PIN_ADD]
    assert api.sleeps == [10]
    assert capsys.readouterr().out == "\tthe pin took 2 attempts\n"


@pytest.mark.parametrize("cut", [
    requests.exceptions.ConnectionError("Read timed out."),
    requests.exceptions.ChunkedEncodingError("Response ended prematurely"),
], ids=["a read timeout", "a dropped connection"])
def test_a_read_timeout_mid_stream_is_handled_as_an_ended_stream(monkeypatch, capsys, cut):
    """requests raises ConnectionError for a read timeout between two chunks
    of a stream, and ChunkedEncodingError for a connection dropped there."""
    stream = _Stream(*PROGRESS, cut)
    api = _Api(monkeypatch, stream, _listed())
    assert _pin() is True
    assert api.urls == [PIN_ADD, PIN_LS]
    assert (stream.read, stream.closed) == (len(PROGRESS), True)
    assert capsys.readouterr().out == ""


def test_a_request_that_gets_no_answer_is_handled_as_an_ended_stream(monkeypatch, capsys):
    api = _Api(monkeypatch, requests.exceptions.ReadTimeout("Read timed out. (read timeout=120)"), _not_pinned(),
               _Stream(PINNED))
    assert _pin() is True
    assert api.urls == [PIN_ADD, PIN_LS, PIN_ADD]
    assert capsys.readouterr().out == "\tthe pin took 2 attempts\n"


def test_five_attempts_without_a_pin_are_false(monkeypatch, capsys):
    """A line whose Pins do not name the CID is not its pin."""
    streams = [_Stream(*PROGRESS), _Stream(b'{"Pins":[]}'), _Stream(b'{"Pins":["%s"]}' % OTHER_CID.encode()),
               _Stream(), _Stream(b'"%s"' % CID.encode())]
    api = _Api(monkeypatch, *_each_followed_by_not_pinned(streams))
    assert _pin() is False
    assert api.calls == [(PIN_ADD, UNCOMPRESSED, True, STREAMED), (PIN_LS, TOKEN, False, ASKED)] * 5
    assert api.sleeps == [10] * 4
    assert all(stream.closed for stream in streams)
    assert capsys.readouterr().out == f"{FAILED} in 5 attempts: the answer ended without the pin\n"


@pytest.mark.parametrize("pins", ['"x%sy"' % CID, '{"%s":1}' % CID, "7", "true"],
                         ids=["a string that holds the CID", "an object keyed by the CID", "a number", "true"])
def test_pins_that_are_no_list_are_not_the_pin(monkeypatch, capsys, pins):
    streams = [_Stream(('{"Pins":%s}' % pins).encode()) for _ in range(5)]
    api = _Api(monkeypatch, *_each_followed_by_not_pinned(streams))
    assert _pin() is False
    assert api.urls == [PIN_ADD, PIN_LS] * 5
    assert capsys.readouterr().out == f"{FAILED} in 5 attempts: the answer ended without the pin\n"


def test_a_read_timeout_at_every_attempt_is_false_and_named(monkeypatch, capsys):
    streams = [_Stream(PROGRESS[0], requests.exceptions.ConnectionError("Read timed out.")) for _ in range(5)]
    api = _Api(monkeypatch, *_each_followed_by_not_pinned(streams))
    assert _pin() is False
    assert api.urls == [PIN_ADD, PIN_LS] * 5
    assert capsys.readouterr().out == f"{FAILED} in 5 attempts: ConnectionError: Read timed out.\n"


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_token_is_false_after_one_request_and_names_the_status(monkeypatch, capsys, status):
    body = "writes need a token. " * 20
    answer = _Answer(status, body)
    api = _Api(monkeypatch, answer)
    assert IPFSClient(API, None).pin(CID) is False
    assert api.calls == [(PIN_ADD, {"Accept-Encoding": "identity"}, True, STREAMED)]
    assert answer.closed
    assert capsys.readouterr().out == f"{FAILED}: status {status}\n\t{body[:300]}\n"


def test_the_body_of_a_status_is_read_as_far_as_its_first_300_characters(monkeypatch, capsys):
    """The body is UTF-8 and arrives a byte at a read: a character of one
    byte and then characters of two. The 1200th byte ends the reading."""
    body = "a" + "\N{LATIN SMALL LETTER E WITH ACUTE}" * 700
    answer = _Stream(*(bytes([byte]) for byte in body.encode()), end=b"", status_code=403)
    api = _Api(monkeypatch, answer)
    assert _pin() is False
    assert api.urls == [PIN_ADD]
    assert (answer.read, answer.closed) == (1200, True)
    assert capsys.readouterr().out == f"{FAILED}: status 403\n\t{body[:300]}\n"


def test_a_status_whose_body_keeps_arriving_is_left_when_the_time_runs_out(monkeypatch, capsys):
    """The pieces of the body arrive 30 s apart; the fourth is the first past
    the 100 s, and no `pin/ls` is asked after it."""
    answer = _Stream(*[b"still failing "] * 10, seconds=30, end=b"", status_code=502)
    api = _Api(monkeypatch, answer)
    assert _pin(timeout_secs=100) is False
    assert api.urls == [PIN_ADD]
    assert (answer.read, answer.closed, api.now) == (4, True, 120)
    assert capsys.readouterr().out == f"{FAILED}: status 502\n\t{'still failing ' * 4}\n"


def test_a_refusal_at_a_later_attempt_ends_the_pin_there(monkeypatch, capsys):
    api = _Api(monkeypatch, _Stream(*PROGRESS), _not_pinned(), _Answer(403, "writes need a token"))
    assert _pin() is False
    assert api.urls == [PIN_ADD, PIN_LS, PIN_ADD]
    assert capsys.readouterr().out == f"{FAILED} in 2 attempts: status 403\n\twrites need a token\n"


def test_a_status_that_is_no_refusal_is_asked_again(monkeypatch, capsys):
    api = _Api(monkeypatch, _Answer(503, UNAVAILABLE), _not_pinned(), _Stream(PINNED))
    assert _pin() is True
    assert api.urls == [PIN_ADD, PIN_LS, PIN_ADD]
    assert capsys.readouterr().out == "\tthe pin took 2 attempts\n"


def test_a_status_that_is_no_refusal_is_false_after_the_attempts_and_named(monkeypatch, capsys):
    api = _Api(monkeypatch, *_each_followed_by_not_pinned([_Answer(503, UNAVAILABLE) for _ in range(5)]))
    assert _pin() is False
    assert api.urls == [PIN_ADD, PIN_LS] * 5
    assert capsys.readouterr().out == f"{FAILED} in 5 attempts: status 503\n\t{UNAVAILABLE}\n"


def test_an_error_line_is_false_and_prints_its_message(monkeypatch, capsys):
    """The message names the CID, which outside a line's Pins is not the pin."""
    message = f"pin: failed to fetch {CID}: context canceled"
    error = ('{"Message":"%s","Code":0,"Type":"error"}' % message).encode()
    streams = [_Stream(*PROGRESS, error) for _ in range(5)]
    api = _Api(monkeypatch, *_each_followed_by_not_pinned(streams))
    assert _pin() is False
    assert api.urls == [PIN_ADD, PIN_LS] * 5
    assert all(stream.closed for stream in streams)
    assert capsys.readouterr().out == f"{FAILED} in 5 attempts: {message}\n"


def test_an_error_line_of_a_pin_the_api_lists_is_true(monkeypatch, capsys):
    error = b'{"Message":"context canceled","Code":0,"Type":"error"}'
    api = _Api(monkeypatch, _Stream(*PROGRESS, error), _listed())
    assert _pin() is True
    assert api.urls == [PIN_ADD, PIN_LS]
    assert capsys.readouterr().out == ""


def test_no_attempt_starts_after_the_pins_time(monkeypatch, capsys):
    """Each answer takes 40 s to end without the pin. Of 100 s the first
    attempt and the wait after it take 50 and the second attempt 40, which
    leaves no time for a third after another wait."""
    api = _Api(monkeypatch, _Stream(PROGRESS[0], PROGRESS[1], seconds=20), _not_pinned(),
               _Stream(PROGRESS[0], PROGRESS[1], seconds=20), _not_pinned(), _Stream(PINNED))
    assert _pin(timeout_secs=100) is False
    assert api.calls == [(PIN_ADD, UNCOMPRESSED, True, (30, 100)), (PIN_LS, TOKEN, False, 30),
                         (PIN_ADD, UNCOMPRESSED, True, (30, 50)), (PIN_LS, TOKEN, False, 10)]
    assert api.times == [0, 40, 50, 90]
    assert (api.sleeps, api.now) == ([10], 90)
    assert capsys.readouterr().out == f"{FAILED} in 2 attempts: the answer ended without the pin\n"


@pytest.mark.parametrize("piece, end", [(PROGRESS[0], b"\n"), (b" ", b"")],
                         ids=["progress lines", "bytes without a line end"])
def test_an_answer_still_arriving_when_the_time_runs_out_is_left(monkeypatch, capsys, piece, end):
    """The API writes progress lines for as long as it looks for the blocks.
    The pieces arrive 30 s apart; the fourth is the first past the 100 s."""
    stream = _Stream(*[piece] * 10, seconds=30, end=end)
    api = _Api(monkeypatch, stream)
    assert _pin(timeout_secs=100) is False
    assert api.calls == [(PIN_ADD, UNCOMPRESSED, True, (30, 100))]
    assert (stream.read, stream.closed, api.now, api.sleeps) == (4, True, 120, [])
    assert capsys.readouterr().out == f"{FAILED}: the pin was not complete when its time ran out\n"


def test_the_line_a_read_past_the_time_completes_is_still_read(monkeypatch):
    """The pin line arrives within the 100 s and its line feed after them."""
    api = _Api(monkeypatch, _Stream(PINNED, b"\n", seconds=60, end=b""))
    assert _pin(timeout_secs=100) is True
    assert api.urls == [PIN_ADD]


def test_a_pin_given_no_time_makes_no_request(monkeypatch, capsys):
    api = _Api(monkeypatch)
    assert _pin(timeout_secs=0) is False
    assert api.calls == []
    assert capsys.readouterr().out == f"{FAILED}: the pin was not complete when its time ran out\n"


def test_under_the_spinner_a_pin_is_one_line(monkeypatch, capsys):
    api = _Api(monkeypatch, _Stream(*PROGRESS, PINNED))
    assert Spinner().spin_till_done(SPINNER_LINE, IPFSClient(API, "writer-token").pin, CID) is True
    assert api.urls == [PIN_ADD]
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert out.endswith(f"\r{Spinner.CHECK}{SPINNER_LINE}\n")


def test_under_the_spinner_a_repeated_pin_adds_the_line_of_its_attempts(monkeypatch, capsys):
    api = _Api(monkeypatch, _Stream(*PROGRESS), _not_pinned(), _Stream(PINNED))
    assert Spinner().spin_till_done(SPINNER_LINE, IPFSClient(API, "writer-token").pin, CID) is True
    assert api.urls == [PIN_ADD, PIN_LS, PIN_ADD]
    out = capsys.readouterr().out
    assert out.count("\n") == 2
    assert "\tthe pin took 2 attempts\n" in out
    assert out.endswith(f"\r{Spinner.CHECK}{SPINNER_LINE}\n")


class _Kubo(http.server.BaseHTTPRequestHandler):
    """The API over HTTP, one request to a connection. `pin/add` answers 200
    and the server's `lines`, a chunk each, and then ends the stream, or with
    the server's `silent` set holds the connection open without another byte
    for 10 s at most; with the server's `chunked` unset the lines are sent
    without chunks or a length, and the connection's end is the answer's.
    `pin/ls` lists the CID. The server keeps each request's path and
    Authorization header, and the Accept-Encoding header of each `pin/add`."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        self.server.seen.append((self.path, self.headers.get("Authorization")))
        self.close_connection = True
        if self.path == PIN_LS_PATH:
            body = b'{"Keys":{"%s":{"Type":"recursive"}}}\n' % CID.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.server.encodings.append(self.headers.get("Accept-Encoding"))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if self.server.chunked:
            self.send_header("Transfer-Encoding", "chunked")
            lines = b"".join(b"%x\r\n%s\n\r\n" % (len(line) + 1, line) for line in self.server.lines)
            end = b"0\r\n\r\n"
        else:
            lines = b"".join(line + b"\n" for line in self.server.lines)
            end = b""
        self.end_headers()
        if self.server.silent:
            self.wfile.write(lines)
            self.server.released.wait(10)
        else:
            self.wfile.write(lines + end)


@pytest.fixture
def kubo(monkeypatch):
    """A _Kubo server on the loopback interface, reached without a proxy."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Kubo)
    server.seen, server.encodings, server.lines = [], [], []
    server.chunked, server.silent, server.released = True, False, threading.Event()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield server
    server.released.set()
    server.shutdown()
    server.server_close()
    thread.join()


def test_over_http_the_pin_is_read_from_the_stream(kubo, capsys):
    kubo.lines = [*PROGRESS, PINNED]
    assert IPFSClient(f"http://127.0.0.1:{kubo.server_port}", "writer-token").pin(CID) is True
    assert kubo.seen == [(PIN_ADD_PATH, "writer-token")]
    assert kubo.encodings == ["identity"]
    assert capsys.readouterr().out == ""


def test_over_http_a_silent_answer_is_left_at_the_silence_bound(kubo, monkeypatch, capsys):
    """The server ends its silent answer after 10 s; the pin is over before."""
    monkeypatch.setattr(module, "PIN_SILENCE_SECONDS", 0.5)
    kubo.lines, kubo.silent = [PROGRESS[0]], True
    started = time.monotonic()
    assert IPFSClient(f"http://127.0.0.1:{kubo.server_port}", "writer-token").pin(CID) is True
    assert time.monotonic() - started < 5
    assert kubo.seen == [(PIN_ADD_PATH, "writer-token"), (PIN_LS_PATH, "writer-token")]
    assert capsys.readouterr().out == ""


def test_over_http_a_line_is_read_when_it_arrives_whatever_the_framing(kubo, monkeypatch, capsys):
    """The answer has no chunks and no length, and stays open and silent
    after the pin line: the line is read from the bytes that arrived, within
    the silence bound and with no `pin/ls`."""
    monkeypatch.setattr(module, "PIN_SILENCE_SECONDS", 2)
    kubo.lines, kubo.chunked, kubo.silent = [PROGRESS[0], PINNED], False, True
    assert IPFSClient(f"http://127.0.0.1:{kubo.server_port}", "writer-token").pin(CID) is True
    assert kubo.seen == [(PIN_ADD_PATH, "writer-token")]
    assert capsys.readouterr().out == ""
