"""The CAS client key of a publishing wallet, ecld-cas-client-v1: the RSA key
pair of the TLS client certificate a securelock session is uploaded under at
the Scontain CAS, derived from the wallet's private key as
docs/cas-client-key.md specifies. @ethernity-cloud/sdk-js derives the same key
pair from the same wallet (tests/vectors/cas-client-key.json). The key pair is
returned to the caller and written nowhere."""
import math

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from eth_account import Account

# HKDF-SHA256's salt and info, and the seed's length in bytes: one half of the
# seed gives each prime.
SALT = b"ecld-cas-client-v1"
INFO = b"rsa-4096"
SEED_LENGTH = 512

E = 65537
# A prime is divisible by no odd prime below SIEVE_LIMIT.
SIEVE_LIMIT = 1 << 20
# The Miller-Rabin bases.
BASES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
# The candidates sieved at a time: base + 2 * j for j below WINDOW.
WINDOW = 1024

_odd_primes = None


def wallet_key_bytes(private_key):
    """The 32 bytes of the wallet key `private_key`, given in any form
    eth_account loads a key from. Raises ValueError, without quoting the key,
    for anything else."""
    try:
        return bytes(Account.from_key(private_key).key)
    except Exception:
        raise ValueError("the publishing wallet's private key is not a secp256k1 private key") from None


def seed(private_key):
    """The seed of the wallet's CAS client key: HKDF-SHA256 over the 32 key
    bytes."""
    return HKDF(algorithm=hashes.SHA256(), length=SEED_LENGTH, salt=SALT, info=INFO).derive(
        wallet_key_bytes(private_key))


def _small_odd_primes():
    """The odd primes below SIEVE_LIMIT, in ascending order."""
    global _odd_primes
    if _odd_primes is None:
        composite = bytearray(SIEVE_LIMIT)
        for i in range(3, math.isqrt(SIEVE_LIMIT) + 1, 2):
            if not composite[i]:
                composite[i * i::2 * i] = b"\x01" * len(range(i * i, SIEVE_LIMIT, 2 * i))
        _odd_primes = [i for i in range(3, SIEVE_LIMIT, 2) if not composite[i]]
    return _odd_primes


def _strong_probable_prime(c, a):
    """Whether the odd c is a strong probable prime to the base a
    (Miller-Rabin)."""
    d, s = c - 1, 0
    while d & 1 == 0:
        d >>= 1
        s += 1
    x = pow(a, d, c)
    if x == 1 or x == c - 1:
        return True
    for _ in range(s - 1):
        x = x * x % c
        if x == c - 1:
            return True
    return False


def prime_from(start):
    """The first of start, start + 2, start + 4, ... that is divisible by no
    odd prime below SIEVE_LIMIT, is not 1 modulo E and is a strong probable
    prime to every base of BASES. `start` is odd."""
    primes = _small_odd_primes()
    # residues[k] is base mod primes[k].
    residues = [start % r for r in primes]
    base = start
    while True:
        composite = bytearray(WINDOW)
        for k, r in enumerate(primes):
            residue = residues[k]
            # The least j with base + 2 * j divisible by r: (r - residue) / 2
            # mod r, 1 / 2 mod r being (r + 1) / 2.
            j = (r - residue) * ((r + 1) >> 1) % r if residue else 0
            if j < WINDOW:
                composite[j::r] = b"\x01" * len(range(j, WINDOW, r))
            residues[k] = (residue + 2 * WINDOW) % r
        for j in range(WINDOW):
            if composite[j]:
                continue
            c = base + 2 * j
            if c % E != 1 and all(_strong_probable_prime(c, a) for a in BASES):
                return c
        base += 2 * WINDOW


def key_from_seed(seed_bytes):
    """The RSA key of a seed: each half of the seed gives one prime of as
    many bits as the half holds. The integers of the RSAPrivateKey, as
    RSAPrivateNumbers."""
    half = len(seed_bytes) // 2
    bits = 8 * half

    def prime(part):
        found = prime_from(int.from_bytes(part, "big") | (3 << (bits - 2)) | 1)
        if found >> bits:
            raise ValueError("the seed gives a prime of more than its half's bits")
        return found

    first = prime(seed_bytes[:half])
    second = prime(seed_bytes[half:])
    if first == second:
        raise ValueError("the seed gives two equal primes")
    p, q = max(first, second), min(first, second)
    d = pow(E, -1, math.lcm(p - 1, q - 1))
    return rsa.RSAPrivateNumbers(
        p=p, q=q, d=d, dmp1=d % (p - 1), dmq1=d % (q - 1), iqmp=pow(q, -1, p),
        public_numbers=rsa.RSAPublicNumbers(e=E, n=p * q))


def cas_client_key(private_key):
    """The CAS client key of the wallet whose private key is `private_key`: a
    cryptography RSAPrivateKey."""
    return key_from_seed(seed(private_key)).private_key()
