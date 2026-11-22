"""What the securelock logs in place of a value that can hold the data
owner's data. Its stdout and stderr reach the node's container log, which
the operator reads: an exception's message, args, repr and traceback can
quote the payload, the input or what they computed, and a result is the
data owner's. Neither is logged."""

import hashlib


def error_class(value):
    """The name of the class of a raised value, as type() reports it;
    'unknown class' when that name cannot be read as a str."""
    try:
        name = type(value).__name__
    except Exception:
        return "unknown class"
    return name if type(name) is str else "unknown class"


def result_digest(result):
    """A result as the securelock logs it: the length in bytes and the
    SHA-256 in hex of the UTF-8 encoding of str(result), the text save_result
    hands to the trustedzone; 'not text' for a value that str() or the UTF-8
    encoding refuses."""
    try:
        data = str(result).encode("utf-8")
    except Exception:
        return "not text"
    return f"{len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}"
