"""public_key_service.main with chain_registered: the hash is not posted
while the service's registry watcher is expected to queue the image; it is
posted once the grace period passes with no row, and when the service holds
a failed extraction of the image. No network: requests.get and requests.post
are replaced, and the clock and sleep are injected."""
import pytest

from ethernity_cloud_sdk_py.commands.pynithy.run import public_key_service as pks

CERT = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----"
FAILED = {"status": "failed", "percent": 100, "publicKey": -1,
          "reason": "the securelock printed no certificate in 10 runs"}


class _Response:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        return self._body


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def _service(monkeypatch, answers):
    """requests.get answers each entry of `answers` in turn (404 when None,
    the last one repeatedly); requests.post is recorded."""
    posts = []
    gets = {"n": 0}

    def get(url, **kwargs):
        i = min(gets["n"], len(answers) - 1)
        gets["n"] += 1
        body = answers[i]
        return _Response(404, {"error": "Hash not found"}) if body is None else _Response(200, body)

    def post(url, json=None, **kwargs):
        posts.append(json)
        return _Response(200, {"queueId": "q1"})

    monkeypatch.setattr(pks.requests, "get", get)
    monkeypatch.setattr(pks.requests, "post", post)
    return posts


def _main(clock, chain_registered):
    return pks.main("canary", "v3", "ETHEREUM_SEPOLIA", "1", ipfs_hash="QmHash",
                    docker_composer_hash="QmCompose", chain_registered=chain_registered,
                    sleep=clock.sleep, now=clock.now)


def test_a_chain_row_that_appears_in_time_is_never_posted(monkeypatch):
    posts = _service(monkeypatch, [None, None, {"status": "running", "percent": 40, "publicKey": 0},
                                   {"status": "done", "percent": 100, "publicKey": CERT}])
    clock = _Clock()
    assert _main(clock, chain_registered=True) == CERT
    assert posts == []
    assert clock.t - 1000.0 == 2 * pks.POLL_SECONDS


def test_the_hash_is_posted_when_the_watcher_has_not_queued_it_within_the_grace_period(monkeypatch):
    rounds = pks.CHAIN_ROW_GRACE_SECONDS // pks.POLL_SECONDS
    posts = _service(monkeypatch, [None] * (rounds + 1) + [{"status": "done", "percent": 100, "publicKey": CERT}])
    clock = _Clock()
    assert _main(clock, chain_registered=True) == CERT
    assert len(posts) == 1
    assert posts[0]["hash"] == "QmHash" and posts[0]["docker_composer_hash"] == "QmCompose"
    assert clock.t - 1000.0 >= pks.CHAIN_ROW_GRACE_SECONDS


def test_a_failed_record_of_a_chain_registered_image_is_posted_again(monkeypatch):
    # The service answers the old record once more right after the submission.
    posts = _service(monkeypatch, [FAILED, FAILED, {"status": "running", "percent": 40, "publicKey": 0},
                                   {"status": "done", "percent": 100, "publicKey": CERT}])
    clock = _Clock()
    assert _main(clock, chain_registered=True) == CERT
    assert len(posts) == 1 and posts[0]["hash"] == "QmHash"
    assert clock.t - 1000.0 == 2 * pks.POLL_SECONDS


def test_a_failed_answer_is_the_outcome_once_the_resubmission_grace_has_passed(monkeypatch):
    posts = _service(monkeypatch, [FAILED])
    clock = _Clock()
    with pytest.raises(SystemExit):
        _main(clock, chain_registered=True)
    assert len(posts) == 1
    assert clock.t - 1000.0 >= pks.RESUBMIT_GRACE_SECONDS


def test_an_image_not_registered_on_chain_is_posted_at_once(monkeypatch):
    posts = _service(monkeypatch, [{"status": "done", "percent": 100, "publicKey": CERT}])
    assert _main(_Clock(), chain_registered=False) == CERT
    assert len(posts) == 1


def test_the_esr_wallet_address_is_returned_beside_the_certificate(monkeypatch):
    _service(monkeypatch, [{"status": "done", "percent": 100, "publicKey": CERT, "esrWalletAddress": "0x" + "1" * 40}])
    assert _main(_Clock(), chain_registered=False) == {"publicKey": CERT, "esrWalletAddress": "0x" + "1" * 40}
