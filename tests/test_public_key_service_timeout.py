"""public_key_service: a poll of the certificate extraction service that
does not reach it or stalls is given up after REQUEST_TIMEOUT_SECONDS and
asked again after POLL_SECONDS, and a submission that cannot reach the
service ends the publish with the cause (2026-11-16: a publish hung for 45
minutes in a poll with no timeout after the service had stored the
certificate). No network: requests.get and requests.post are replaced."""
import pytest
import requests

from ethernity_cloud_sdk_py.commands.pynithy.run import public_key_service as pks

CERT = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----"


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = ""

    def json(self):
        return self._body


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_a_stalled_poll_is_given_up_and_asked_again(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise requests.Timeout("stalled")
        if len(calls) == 2:
            raise requests.ConnectionError("dropped")
        return _Response(200, {"status": "done", "percent": 100, "publicKey": CERT})

    monkeypatch.setattr(pks.requests, "get", get)
    monkeypatch.setattr(pks.requests, "post", lambda url, json=None, **kwargs: _Response(200, {"queueId": "q1"}))
    clock = _Clock()
    assert pks.main("canary", "v3", "LITVM_LITEFORGE", "3", ipfs_hash="QmHash",
                    docker_composer_hash="QmCompose", chain_registered=False,
                    sleep=clock.sleep, now=clock.now) == CERT
    assert len(calls) == 3
    assert all(c["timeout"] == pks.REQUEST_TIMEOUT_SECONDS for c in calls)
    assert clock.t - 1000.0 == 2 * pks.POLL_SECONDS


def test_check_ipfs_hash_status_answers_none_for_a_stalled_request(monkeypatch):
    def get(url, **kwargs):
        assert kwargs["timeout"] == pks.REQUEST_TIMEOUT_SECONDS
        raise requests.Timeout("stalled")

    monkeypatch.setattr(pks.requests, "get", get)
    assert pks.check_ipfs_hash_status("QmHash") is None


def test_a_submission_that_cannot_reach_the_service_ends_the_publish(monkeypatch, capsys):
    def post(url, json=None, **kwargs):
        assert kwargs["timeout"] == pks.REQUEST_TIMEOUT_SECONDS
        raise requests.ConnectionError("dropped")

    monkeypatch.setattr(pks.requests, "post", post)
    with pytest.raises(SystemExit) as stop:
        pks.submit_ipfs_hash("QmHash", "canary", "v3", "LITVM_LITEFORGE", "3", "QmCompose")
    assert stop.value.code == 1
    out = capsys.readouterr().out
    assert "Could not reach the certificate extraction service to submit QmHash: dropped" in out
