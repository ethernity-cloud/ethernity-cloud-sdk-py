import logging
import re

# The file the node agent mounts read-only into the enclave's container.
STORE_CREDENTIAL_PATH = '/run/ecld/store.json'
# A file of more bytes than this is not a credential.
_MAX_BYTES = 4096
# The whole file: one JSON object of the members accessKey and secretKey, in
# that order, each a string of ASCII letters and digits, with JSON white
# space between the tokens.
_CREDENTIAL = re.compile(
    rb'[ \t\r\n]*\{[ \t\r\n]*"accessKey"[ \t\r\n]*:[ \t\r\n]*"([A-Za-z0-9]{3,20})"[ \t\r\n]*,'
    rb'[ \t\r\n]*"secretKey"[ \t\r\n]*:[ \t\r\n]*"([A-Za-z0-9]{8,40})"[ \t\r\n]*\}[ \t\r\n]*')
# Access key and secret key of a store whose agent mounts no file.
_LITERAL = 'swiftstreamadmin'


def _from_file(path):
    """(access key, secret key, None) of the file at `path`, or (None, None,
    reason): why it gives no credential."""
    try:
        with open(path, 'rb') as handle:
            head = handle.read(_MAX_BYTES + 1)
    except (FileNotFoundError, NotADirectoryError):
        return None, None, 'absent'
    except Exception:
        return None, None, 'unreadable'
    if len(head) > _MAX_BYTES:
        return None, None, 'too large'
    found = _CREDENTIAL.fullmatch(head)
    if found is None:
        return None, None, 'malformed'
    return found.group(1).decode('ascii'), found.group(2).decode('ascii'), None


def load(path=STORE_CREDENTIAL_PATH, log=logging.info):
    """(access key, secret key, source) of this enclave's store credential:
    the file's at `path` with source 'file', else the literal pair with
    source 'literal'. Logs one line, which names the path and never a key."""
    access_key, secret_key, reason = _from_file(path)
    if reason is None:
        log(f'store credential: file ({path})')
        return access_key, secret_key, 'file'
    log(f'store credential: literal ({path}: {reason})')
    return _LITERAL, _LITERAL, 'literal'
