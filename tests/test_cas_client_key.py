"""The CAS client key of a publishing wallet
(commands/pynithy/cas_client_key.py), derived as docs/cas-client-key.md
specifies.

tests/vectors/cas-client-key.json holds test wallets with the public key each
derives; @ethernity-cloud/sdk-js holds the same bytes as
test/vectors/cas-client-key.json and derives the same keys, and both suites
pin the SHA-256 of the file and of the specification. Beside the vectors: the
seed against RFC 5869 computed here with HMAC, the integers against OpenSSL's
check of an RSA key (RSAPrivateNumbers.private_key), and the three tests a
prime passes on starts below 2^64, where Miller-Rabin to Sinclair's seven
bases is exact."""
import ast
import hashlib
import hmac
import json
import math
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ethernity_cloud_sdk_py.commands.pynithy import cas_client_key

REPO = Path(__file__).resolve().parents[1]
VECTOR_FILE = Path(__file__).resolve().parent / "vectors" / "cas-client-key.json"
SPECIFICATION = REPO / "docs" / "cas-client-key.md"
# The same two values are pinned in @ethernity-cloud/sdk-js
# (test/casClientKey.test.js).
VECTOR_FILE_SHA256 = "a322c0ded36e3786a1fce86aa9e4c1485fbeddb19a3bbc8c1456c7938c14a7b7"
SPECIFICATION_SHA256 = "45f25b14dc81b31851805a55bf16f3ab8f4d8c29c0cdc31809c45d42d4b0150f"

DOCUMENT = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))
VECTORS = DOCUMENT["vectors"]
# The wallet whose primes are found soonest.
QUICK = next(v for v in VECTORS if v["walletPrivateKey"] == "0x" + "ee" * 32)
E = 65537
EACH_VECTOR = pytest.mark.parametrize("vector", VECTORS, ids=[v["walletPrivateKey"][:10] for v in VECTORS])

_derived = {}


def numbers_of(vector):
    """The integers the vector's wallet derives, computed once."""
    key = vector["walletPrivateKey"]
    if key not in _derived:
        _derived[key] = cas_client_key.key_from_seed(cas_client_key.seed(key))
    return _derived[key]


def spki(public_key):
    return public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def hkdf(ikm, salt, info, length):
    """HKDF-SHA256 as RFC 5869 writes it: extract, then expand."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm, block, counter = b"", b"", 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def first_candidate(half):
    """c0 of the specification for one half of a seed."""
    return int.from_bytes(half, "big") | (3 << (8 * len(half) - 2)) | 1


def is_prime_below_2_64(n):
    """Whether n, below 2^64, is prime: Miller-Rabin to the seven bases of
    Jim Sinclair, which err on no number below 2^64."""
    assert 1 < n < 1 << 64
    for small in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % small == 0:
            return n == small
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for base in (2, 325, 9375, 28178, 450775, 9780504, 1795265022):
        x = pow(base % n, d, n)
        if x in (0, 1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def first_prime(start):
    """The first of start, start + 2, ... below 2^64 that is prime and not 1
    modulo 65537."""
    c = start
    while c % E == 1 or not is_prime_below_2_64(c):
        c += 2
    return c


def test_the_vector_file_and_the_specification_are_the_bytes_sdk_js_holds():
    for path, pinned in ((VECTOR_FILE, VECTOR_FILE_SHA256), (SPECIFICATION, SPECIFICATION_SHA256)):
        content = path.read_bytes()
        assert b"\r" not in content, f"{path} holds a CR"
        assert hashlib.sha256(content).hexdigest() == pinned, (
            f"{path} is not the pinned file; the copy in @ethernity-cloud/sdk-js changes with it")


def test_the_vector_file_holds_at_least_three_test_wallets_each_with_its_own_public_key():
    assert DOCUMENT["scheme"] == "ecld-cas-client-v1"
    assert len(VECTORS) >= 3
    for vector in VECTORS:
        assert len(vector["walletPrivateKey"]) == 66 and vector["walletPrivateKey"].startswith("0x")
        der = bytes.fromhex(vector["publicKeySpkiDer"])
        assert hashlib.sha256(der).hexdigest() == vector["publicKeySha256"]
        public_key = serialization.load_der_public_key(der)
        assert isinstance(public_key, rsa.RSAPublicKey)
        assert public_key.key_size == 4096
        assert public_key.public_numbers().e == E
    assert len({v["walletPrivateKey"] for v in VECTORS}) == len(VECTORS)
    assert len({v["publicKeySpkiDer"] for v in VECTORS}) == len(VECTORS)


@EACH_VECTOR
def test_the_wallet_derives_the_public_key_of_the_vector(vector):
    der = spki(numbers_of(vector).private_key().public_key())
    assert der.hex() == vector["publicKeySpkiDer"]
    assert hashlib.sha256(der).hexdigest() == vector["publicKeySha256"]


@EACH_VECTOR
def test_each_prime_is_the_candidate_of_its_half_of_the_seed_the_vector_names(vector):
    numbers = numbers_of(vector)
    seed = cas_client_key.seed(vector["walletPrivateKey"])
    primes = [first_candidate(half) + 2 * offset
              for half, offset in zip((seed[:256], seed[256:]), vector["primeOffsets"])]
    assert [numbers.p, numbers.q] == sorted(primes, reverse=True)


@EACH_VECTOR
def test_the_integers_are_those_of_a_4096_bit_rsa_key_with_e_65537(vector):
    numbers = numbers_of(vector)
    p, q, d = numbers.p, numbers.q, numbers.d
    assert numbers.public_numbers.e == E
    assert numbers.public_numbers.n == p * q
    assert (p * q).bit_length() == 4096
    for prime in (p, q):
        assert prime >> 2046 == 3, "a prime of 2048 bits with its two highest bits set"
        assert prime % E != 1
    assert p > q
    carmichael = math.lcm(p - 1, q - 1)
    assert 0 < d < carmichael
    assert d * E % carmichael == 1
    assert numbers.dmp1 == d % (p - 1)
    assert numbers.dmq1 == d % (q - 1)
    assert 0 < numbers.iqmp < p
    assert numbers.iqmp * q % p == 1
    # OpenSSL checks the integers, the primality of p and q among them.
    private_key = numbers.private_key()
    signature = private_key.sign(b"ecld", padding.PKCS1v15(), hashes.SHA256())
    serialization.load_der_public_key(bytes.fromhex(vector["publicKeySpkiDer"])).verify(
        signature, b"ecld", padding.PKCS1v15(), hashes.SHA256())


def test_openssl_refuses_the_integers_once_a_prime_is_replaced_by_a_composite():
    """The integers of a key whose p is a multiple of 3, consistent in
    everything but the primality of p: what the check of the vectors' keys
    above would not let pass."""
    q = numbers_of(QUICK).q
    composite = next(c for c in (numbers_of(QUICK).p + 2, numbers_of(QUICK).p + 4) if c % 3 == 0)
    carmichael = math.lcm(composite - 1, q - 1)
    assert math.gcd(E, carmichael) == 1 and math.gcd(q, composite) == 1
    d = pow(E, -1, carmichael)
    replaced = rsa.RSAPrivateNumbers(
        p=composite, q=q, d=d, dmp1=d % (composite - 1), dmq1=d % (q - 1), iqmp=pow(q, -1, composite),
        public_numbers=rsa.RSAPublicNumbers(e=E, n=composite * q))
    with pytest.raises(ValueError):
        replaced.private_key()


def test_cas_client_key_gives_the_same_private_key_at_each_call_the_one_of_the_wallets_vector():
    def pkcs8():
        return cas_client_key.cas_client_key(QUICK["walletPrivateKey"]).private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())

    first = pkcs8()
    assert first == pkcs8()
    assert spki(serialization.load_der_private_key(first, None).public_key()).hex() == QUICK["publicKeySpkiDer"]


def test_a_key_is_read_in_the_forms_eth_account_reads_and_anything_else_is_refused_without_being_quoted():
    text = VECTORS[1]["walletPrivateKey"][2:]
    seed = cas_client_key.seed("0x" + text)
    assert cas_client_key.seed(text) == seed
    assert cas_client_key.seed("0x" + text.upper()) == seed
    assert cas_client_key.seed(text.upper()) == seed
    assert cas_client_key.seed("0X" + text) == seed
    assert cas_client_key.seed(bytes.fromhex(text)) == seed
    # A key written without its leading zero digit is the same 32 bytes.
    assert VECTORS[0]["walletPrivateKey"] == "0x" + "0" * 63 + "1"
    assert cas_client_key.seed("0" * 62 + "1") == cas_client_key.seed(VECTORS[0]["walletPrivateKey"])
    for refused in (text[2:], "0x" + text + "00", text[:62] + "zz", " " + text, "", None, "0x" + "ff" * 32):
        with pytest.raises(ValueError) as error:
            cas_client_key.seed(refused)
        assert str(error.value) == "the publishing wallet's private key is not a secp256k1 private key"
        assert error.value.__cause__ is None and error.value.__suppress_context__


def test_the_seed_is_hkdf_sha256_of_the_32_key_bytes_under_the_salt_and_the_info_of_the_specification():
    assert cas_client_key.SALT == b"ecld-cas-client-v1"
    assert cas_client_key.INFO == b"rsa-4096"
    for vector in VECTORS:
        ikm = bytes.fromhex(vector["walletPrivateKey"][2:])
        assert len(ikm) == 32
        seed = cas_client_key.seed(vector["walletPrivateKey"])
        assert len(seed) == 512
        assert seed == hkdf(ikm, b"ecld-cas-client-v1", b"rsa-4096", 512)


def test_another_salt_or_another_info_gives_another_seed_and_another_key():
    ikm = bytes.fromhex(QUICK["walletPrivateKey"][2:])
    seed = cas_client_key.seed(QUICK["walletPrivateKey"])
    other_salt = hkdf(ikm, b"ecld-cas-client-v2", b"rsa-4096", 512)
    other_info = hkdf(ikm, b"ecld-cas-client-v1", b"rsa-2048", 512)
    seeds = (seed, other_salt, other_info)
    # No half is shared either: each prime starts from other bytes.
    assert len({s[:256] for s in seeds}) == 3 and len({s[256:] for s in seeds}) == 3
    keys = {QUICK["publicKeySpkiDer"]}
    for other in (other_salt, other_info):
        keys.add(spki(cas_client_key.key_from_seed(other).private_key().public_key()).hex())
    assert len(keys) == 3


def test_prime_from_gives_the_first_prime_from_starts_of_48_to_63_bits():
    passed_over = 0
    for index, size in enumerate((48, 51, 54, 57, 59, 60, 61, 62, 63, 63)):
        # An odd number of `size` bits.
        digest = hashlib.sha256(f"ecld-cas-client-v1 start {index}".encode()).digest()
        start = int.from_bytes(digest, "big") >> (256 - size) | 1 << (size - 1) | 1
        found = cas_client_key.prime_from(start)
        assert found == first_prime(start), f"{size} bits"
        assert found >= start and (found - start) % 2 == 0
        passed_over += (found - start) // 2
    # The searches pass over composites: no start is its own answer throughout.
    assert passed_over > 100, f"{passed_over} candidates passed over"


def test_prime_from_passes_over_a_prime_that_is_1_modulo_65537():
    # The least prime 65537 * k + 1 above 2^24.
    prime = E * ((1 << 24) // E + 1) + 1
    while not is_prime_below_2_64(prime):
        prime += E
    assert prime % E == 1
    found = cas_client_key.prime_from(prime)
    assert found > prime
    assert found == first_prime(prime)


def test_prime_from_passes_over_a_product_of_two_primes_above_2_20_which_no_prime_below_2_20_divides():
    r1 = first_prime((1 << 20) + 1)
    r2 = first_prime(r1 + 2)
    product = r1 * r2
    assert product % E != 1
    found = cas_client_key.prime_from(product)
    assert found > product
    assert found == first_prime(product)


def test_a_seed_of_two_equal_halves_and_one_whose_search_leaves_the_bits_of_its_half_give_no_key():
    half = hashlib.sha256(b"ecld-cas-client-v1 half").digest()
    with pytest.raises(ValueError, match="^the seed gives two equal primes$"):
        cas_client_key.key_from_seed(half + half)
    # 2^128 - 1 is composite, and every later candidate has 129 bits.
    with pytest.raises(ValueError, match="^the seed gives a prime of more than its half's bits$"):
        cas_client_key.key_from_seed(b"\xff" * 32)
    # Two halves that differ give a key: here of 128 bits, whose primes are
    # below 2^64.
    numbers = cas_client_key.key_from_seed(hashlib.sha256(b"ecld-cas-client-v1 seed").digest()[:16])
    assert numbers.public_numbers.n.bit_length() == 128
    assert is_prime_below_2_64(numbers.p) and is_prime_below_2_64(numbers.q)
    assert numbers.p > numbers.q


def test_cas_client_key_py_opens_no_file_and_imports_no_module_that_writes_one():
    source = (REPO / "ethernity_cloud_sdk_py" / "commands" / "pynithy" / "cas_client_key.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported == {
        "math", "cryptography.hazmat.primitives", "cryptography.hazmat.primitives.asymmetric",
        "cryptography.hazmat.primitives.kdf.hkdf", "eth_account"}
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "open" not in called
