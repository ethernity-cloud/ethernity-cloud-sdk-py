# The CAS client key of a publishing wallet (ecld-cas-client-v1)

`ecld-publish` of @ethernity-cloud/sdk-js and of ethernity-cloud-sdk-py
uploads a dApp's securelock session to the Scontain CAS over TLS with a client
certificate. The CAS gives the session's read and update rights to the public
key of that certificate. This document specifies the RSA key pair both SDKs
derive for that certificate from the private key of the publishing wallet: the
same wallet gives the same key pair in both. The document is byte-identical in
the two repositories (`docs/cas-client-key.md`), and so is the vector file
named at its end.

## Input

K, the wallet's private key: 32 bytes, the secp256k1 scalar in big-endian
order.

## Seed

    S = HKDF-SHA256(IKM = K, salt = "ecld-cas-client-v1", info = "rsa-4096", L = 512)

HKDF is RFC 5869's, with SHA-256. The salt and the info are the ASCII bytes of
the strings between the quotes. S is 512 bytes: bytes 0 to 255 give the first
prime, bytes 256 to 511 the second.

## A prime from 256 bytes

1. x is the 256 bytes read as an unsigned big-endian integer.
2. c0 = x OR 2^2047 OR 2^2046 OR 1: the two highest bits and the lowest bit
   are set.
3. The candidates are c0, c0 + 2, c0 + 4, ... in that order. The prime is the
   first candidate c that passes all three tests:
   - c is divisible by none of the odd primes below 2^20 (3 to 1048573);
   - c mod 65537 is not 1;
   - c is a strong probable prime to each of the twelve bases 2, 3, 5, 7, 11,
     13, 17, 19, 23, 29, 31 and 37 (Miller-Rabin). With c - 1 = 2^s * d and d
     odd, let y = a^d mod c: c is a strong probable prime to the base a when
     y is 1 or c - 1, or when squaring y modulo c, up to s - 1 times, gives
     c - 1.
4. A prime of more than 2048 bits ends the derivation with an error.

## The key

p is the larger of the two primes and q the smaller; two equal primes end the
derivation with an error.

    n    = p * q
    e    = 65537
    d    = e^-1 mod lcm(p - 1, q - 1)
    dP   = d mod (p - 1)
    dQ   = d mod (q - 1)
    qInv = q^-1 mod p

n has 4096 bits. The private key is the two-prime RSAPrivateKey of RFC 8017
(A.1.2) with these values; the public key is (n, e).

## Properties

- K enters the derivation only as HKDF's input keying material: the key pair
  discloses nothing of K.
- The salt names this use and its version, and the info the key type and its
  size. No other derivation of the SDKs uses them.
- One wallet has one key pair, whatever the project and whichever SDK.

## The certificate

Each SDK derives the key pair at each upload, keeps it in no file of the
project or of the package, and builds a self-signed X.509 certificate over it
for that upload. The CAS authenticates the public key of the client
certificate, not the certificate (SCONE session language 0.3, "Access
Control"), so the certificates of two uploads need not be equal.

## Vectors

`cas-client-key.json` (`test/vectors/` in sdk-js, `tests/vectors/` in sdk-py),
byte-identical in both: wallet keys that are test values, each with the
offsets of its two primes (the prime of each half of the seed is c0 + 2 *
offset), the DER encoding of the derived public key as a SubjectPublicKeyInfo
(RFC 5280, rsaEncryption with NULL parameters) in hexadecimal, and the
SHA-256 of that encoding.
