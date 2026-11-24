"""The upload of a securelock session to the Scontain CAS, and the TLS client
key it is made under.

The CAS gives a session's read and update rights to the public key of the
client certificate that uploaded it, unless the session's access_policy names
other keys. ecld-publish uploads a session under the CAS client key of the
publishing wallet (cas_client_key.py), derived at each upload, with a
self-signed certificate built for the upload. requests takes a client
certificate and its key as files: they are written to a directory
tempfile.mkdtemp makes, which only its owner can read, and the directory is
removed when the upload ends, however it ends. Nothing of the key is written
under the project or the package.

A project published by an SDK up to 1.9.1 holds the random client key of that
SDK as key.pem and cert.pem in build/certs. The update of such a project's
session is made under those files and carries an access_policy whose read and
update lists name the certificate of the wallet's key (the hand-over).
.config.json records the hand-over as CAS_CLIENT_KEY "handover" and an upload
under the wallet's key as CAS_CLIENT_KEY "wallet"; with either record the
files are not read. They are never written or removed."""
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID
from requests.packages.urllib3 import disable_warnings
from requests.packages.urllib3.exceptions import InsecureRequestWarning

from ethernity_cloud_sdk_py.commands.pynithy.cas_client_key import cas_client_key

SESSION_URL = "https://scone-cas.cf:8081/session"

# The .config.json key that records which key holds the session, and its
# values.
RECORD = "CAS_CLIENT_KEY"
HANDOVER = "handover"
WALLET = "wallet"


def upload_key(predecessor, legacy_files, recorded):
    """The key a session is uploaded under: HANDOVER, the project's key.pem
    and cert.pem with the access policy that names the wallet's key, for a
    session with a predecessor in a project that holds both files and no
    record; WALLET, the wallet's key, in every other case."""
    if predecessor and legacy_files and recorded not in (HANDOVER, WALLET):
        return HANDOVER
    return WALLET


def self_signed_certificate(private_key, organization):
    """A self-signed X.509 certificate over `private_key`, valid from one
    year before now to two years after, with `organization` as the O of its
    subject."""
    public_key = private_key.public_key()
    name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AU"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Some-State"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
    ])
    valid_from = datetime.now(timezone.utc) - timedelta(days=365)
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(public_key)
        .serial_number(1)
        .not_valid_before(valid_from)
        .not_valid_after(valid_from + timedelta(days=3 * 365))
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(public_key), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key=private_key, algorithm=hashes.SHA256())
    )


def access_policy(certificate_pem):
    """The access_policy of a session whose read and update rights belong to
    the key of the certificate `certificate_pem` and to no other."""
    entry = "\n".join(["    - |"] + [f"      {line}" for line in certificate_pem.strip().splitlines()])
    return f"access_policy:\n  read:\n{entry}\n  update:\n{entry}\n"


@contextmanager
def _client_files(private_key, certificate):
    """The paths of `certificate` and of its key as PEM files, in a directory
    of their own that only its owner can read; the directory is removed when
    the block ends."""
    directory = tempfile.mkdtemp(prefix="ecld-cas-client-")
    try:
        files = {
            "cert.pem": certificate.public_bytes(serialization.Encoding.PEM),
            "key.pem": private_key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        }
        for name, content in files.items():
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(os.path.join(directory, name), flags, 0o600), "wb") as f:
                f.write(content)
        yield os.path.join(directory, "cert.pem"), os.path.join(directory, "key.pem")
    finally:
        shutil.rmtree(directory)


def _post(session, url, session_file):
    with open(session_file, "rb") as f:
        return session.post(url, data=f.read(), headers={"Content-Type": "application/octet-stream"})


def upload(*, url, session_file, predecessor, legacy_dir, recorded, wallet_key, organization, write_record, log):
    """Posts the session file `session_file` to the CAS at `url` and returns
    the answer (a requests.Response).

    predecessor: whether the session names a predecessor. legacy_dir: the
    directory of the project's key.pem and cert.pem. recorded: the project's
    CAS_CLIENT_KEY. wallet_key: the publishing wallet's private key.
    organization: the O of the wallet key's certificate. write_record(value)
    stores CAS_CLIENT_KEY; log(line) takes a line to print.

    For a hand-over (upload_key) the access policy is appended to the session
    file before it is posted. An answer that carries the session's hash
    records the key the upload was made under: HANDOVER after a hand-over,
    WALLET after an upload under the wallet's key, then with one line that
    says key.pem and cert.pem are no longer used when the project holds
    them."""
    legacy_dir = Path(legacy_dir)
    legacy_key = legacy_dir / "key.pem"
    legacy_cert = legacy_dir / "cert.pem"
    legacy_files = legacy_key.exists() and legacy_cert.exists()
    under = upload_key(predecessor, legacy_files, recorded)

    private_key = cas_client_key(wallet_key)
    certificate = self_signed_certificate(private_key, organization)

    # The CAS's certificate is not checked, as with curl -k.
    disable_warnings(InsecureRequestWarning)
    session = requests.Session()
    session.verify = False
    try:
        if under == HANDOVER:
            with open(session_file, "r") as f:
                rendered = f.read()
            policy = access_policy(certificate.public_bytes(serialization.Encoding.PEM).decode("ascii"))
            with open(session_file, "a") as f:
                f.write(("" if rendered.endswith("\n") else "\n") + "\n" + policy)
            log(f"The session is uploaded under key.pem and cert.pem of {legacy_dir.resolve()}; "
                "its access policy gives its read and update rights to the CAS client key of the publishing wallet")
            session.cert = (str(legacy_cert), str(legacy_key))
            response = _post(session, url, session_file)
        else:
            log("The session is uploaded under the CAS client key of the publishing wallet")
            with _client_files(private_key, certificate) as client:
                session.cert = client
                response = _post(session, url, session_file)
    finally:
        session.close()

    try:
        answer = response.json()
    except ValueError:
        answer = None
    if isinstance(answer, dict) and answer.get("hash") is not None:
        if under == HANDOVER:
            write_record(HANDOVER)
            log("The session's read and update rights now belong to the CAS client key of the publishing wallet: "
                "the next publish uploads under it")
        elif recorded != WALLET:
            write_record(WALLET)
            if legacy_files:
                log(f"key.pem and cert.pem of {legacy_dir.resolve()} are no longer used: "
                    "the session is uploaded under the CAS client key of the publishing wallet")
    return response
