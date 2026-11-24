"""A stand-in for the session endpoint of a SCONE CAS: an HTTPS server on a
loopback port that requires a TLS client certificate and keeps sessions by
the rules of the SCONE session language 0.3 that
commands/pynithy/scontain_session.py relies on.

POST /session takes a session in YAML. A request without a client certificate
is answered 401. A session whose name is new names no predecessor (400
otherwise) and is created. A session whose name is taken names the stored
hash as its predecessor (409 otherwise) and comes from a key that holds the
stored session's update right (403 otherwise). That right belongs to the keys
of the certificates the stored session's access_policy lists under `update`,
CREATOR there being its uploader's key, and to the uploader's key alone when
it has no access_policy. An accepted session is answered 201 with
{"name", "hash"}: the hash is the SHA-256 of the body and the uploader's key,
or is left out while `omit_hash` is set.

Every request is recorded in `requests`: the uploader's key (the DER of its
SubjectPublicKeyInfo, in hex; None without a certificate), its certificate,
the TLS protocol, the method, the path, the content type, the body, the
status answered, and what `observe()` returned while the request was open.

The server is pyOpenSSL's, which the SDK installs: Python's ssl module asks
for a client certificate only to verify it against a trust store, and the
certificates the SDK presents are self-signed."""
import hashlib
import json
import socket
import threading
import warnings
from datetime import datetime, timedelta, timezone

import requests
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from OpenSSL import SSL
from requests.packages.urllib3.exceptions import InsecureRequestWarning


def key_of(certificate):
    """The DER of the SubjectPublicKeyInfo of a certificate (an
    x509.Certificate, or its PEM), in hex."""
    if isinstance(certificate, (str, bytes)):
        pem = certificate.encode("ascii") if isinstance(certificate, str) else certificate
        certificate = x509.load_pem_x509_certificate(pem)
    return certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo).hex()


class Identity:
    """A throwaway RSA key and a self-signed certificate over it."""

    def __init__(self, common_name):
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        now = datetime.now(timezone.utc)
        self.certificate = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(self.private_key.public_key()).serial_number(1)
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .sign(self.private_key, hashes.SHA256()))
        self.key_pem = self.private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        self.cert_pem = self.certificate.public_bytes(serialization.Encoding.PEM)
        self.key = key_of(self.certificate)

    def write(self, directory):
        """key.pem and cert.pem in `directory`, as an SDK up to 1.9.1 kept
        them in build/certs; returns the two paths, certificate first."""
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "key.pem").write_bytes(self.key_pem)
        (directory / "cert.pem").write_bytes(self.cert_pem)
        return str(directory / "cert.pem"), str(directory / "key.pem")


def post(url, body, cert=None):
    """One POST of `body` to `url` under the client certificate and key files
    `cert` (none when left out): the status and the parsed JSON answer."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)
        response = requests.post(url, data=body, cert=cert, verify=False,
                                 headers={"Content-Type": "application/octet-stream"})
    return response.status_code, response.json()


class Cas:
    def __init__(self):
        self.sessions = {}
        self.requests = []
        self.omit_hash = False
        self.observe = lambda: None
        identity = Identity("cas stand-in")
        self._context = SSL.Context(SSL.TLS_SERVER_METHOD)
        self._context.use_privatekey(identity.private_key)
        self._context.use_certificate(identity.certificate)
        # Any certificate the client presents is taken; none is not an error.
        self._context.set_verify(SSL.VERIFY_PEER, lambda connection, certificate, error, depth, ok: True)
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self._address = self._listener.getsockname()
        self._closed = False
        self.url = f"https://127.0.0.1:{self._address[1]}/session"
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self):
        """Ends the server: a connection of its own takes the thread out of
        accept()."""
        self._closed = True
        try:
            socket.create_connection(self._address, timeout=5).close()
        except OSError:
            pass
        self._thread.join(timeout=5)
        self._listener.close()

    def _serve(self):
        while True:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            try:
                if self._closed:
                    return
                self._answer(client)
            except (SSL.Error, OSError):
                pass
            finally:
                client.close()

    def _answer(self, client):
        connection = SSL.Connection(self._context, client)
        connection.set_accept_state()
        connection.do_handshake()
        received = b""
        while b"\r\n\r\n" not in received:
            received += connection.recv(65536)
        head, body = received.split(b"\r\n\r\n", 1)
        request_line, *header_lines = head.decode("latin-1").split("\r\n")
        headers = {name.strip().lower(): value.strip() for name, value in
                   (line.split(":", 1) for line in header_lines)}
        while len(body) < int(headers.get("content-length", "0")):
            body += connection.recv(65536)
        method, path, _ = request_line.split(" ", 2)
        certificate = connection.get_peer_certificate(as_cryptography=True)
        record = {
            "key": key_of(certificate) if certificate is not None else None,
            "certificate": certificate,
            "protocol": connection.get_protocol_version_name(),
            "method": method,
            "path": path,
            "content_type": headers.get("content-type"),
            "body": body,
            "status": None,
            "observed": self.observe(),
        }
        self.requests.append(record)
        record["status"], answer = self._decide(record)
        payload = json.dumps(answer).encode("utf-8")
        connection.sendall(
            b"HTTP/1.1 %d Answer\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
            % (record["status"], len(payload)) + payload)
        connection.shutdown()

    def _decide(self, record):
        if record["key"] is None:
            return 401, {"msg": "a client certificate is required"}
        if (record["method"], record["path"]) != ("POST", "/session"):
            return 404, {"msg": "no such endpoint"}
        try:
            session = yaml.safe_load(record["body"].decode("utf-8"))
        except yaml.YAMLError as error:
            return 400, {"msg": f"the session is not YAML: {error}"}
        stored = self.sessions.get(session["name"])
        if stored is None and "predecessor" in session:
            return 400, {"msg": "a new session names no predecessor"}
        if stored is not None and str(session.get("predecessor")) != stored["hash"]:
            return 409, {"msg": "the predecessor is not the stored session"}
        if stored is not None and record["key"] not in stored["update"]:
            return 403, {"msg": "the client key does not hold the session's update right"}
        policy = session.get("access_policy") or {}

        def holders(right):
            return [record["key"] if entry == "CREATOR" else key_of(entry) for entry in policy.get(right, ["CREATOR"])]

        digest = hashlib.sha256(record["body"] + record["key"].encode("ascii")).hexdigest()
        self.sessions[session["name"]] = {
            "hash": digest, "creator": record["key"], "read": holders("read"), "update": holders("update")}
        answer = {"name": session["name"]}
        if not self.omit_hash:
            answer["hash"] = digest
        return 201, answer
