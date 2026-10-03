"""IPFSClient.upload_dir: a connection dropped mid-upload is retried like a
gateway error, and every attempt closes the files it opened. No network: the
HTTP POST is replaced."""
from requests.exceptions import ConnectionError, SSLError

from ethernity_cloud_sdk_py.commands.pynithy import ipfs_client
from ethernity_cloud_sdk_py.commands.pynithy.ipfs_client import IPFSClient

ROOT = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"


class _Response:
    status_code = 200
    text = '{"Name":"layer.tar","Hash":"bafkreiexample"}\n{"Name":"","Hash":"%s"}\n' % ROOT


def _tree(tmp_path):
    (tmp_path / "layer.tar").write_bytes(b"\0" * 4096)
    (tmp_path / "manifest.json").write_text("{}")
    return str(tmp_path)


def _posts(monkeypatch, outcomes, opened):
    """Replace requests.post with one that drains the multipart body, records
    the file objects it was given, and answers with each outcome in turn."""
    calls = []

    def post(url, data=None, **kwargs):
        calls.append(url)
        opened.extend(f[1] for f in data.encoder.fields.values())
        while data.read(65536):
            pass
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ipfs_client.requests, "post", post)
    return calls


def test_a_dropped_connection_is_retried(monkeypatch, tmp_path):
    opened = []
    calls = _posts(monkeypatch, [SSLError("EOF occurred in violation of protocol"), _Response()], opened)
    assert IPFSClient("https://ipfs.example").upload_dir(_tree(tmp_path)) == ROOT
    assert len(calls) == 2
    assert opened and all(f.closed for f in opened)


def test_the_upload_gives_up_after_its_attempts(monkeypatch, tmp_path):
    opened = []
    calls = _posts(monkeypatch, [ConnectionError("reset")] * 3, opened)
    assert IPFSClient("https://ipfs.example").upload_dir(_tree(tmp_path), attempts=3) is False
    assert len(calls) == 3
    assert opened and all(f.closed for f in opened)
