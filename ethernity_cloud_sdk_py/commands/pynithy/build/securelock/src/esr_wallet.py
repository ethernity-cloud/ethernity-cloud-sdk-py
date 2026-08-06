"""ESR identity wallet derivation (ESR RFC §5.1).

The enclave's Ethereum wallet is derived from the enclave's IDENTITY PRIVATE
KEY (the cert key), never from MR_ENCLAVE:

    eth_priv    = keccak256(DOMAIN_SEP || identity_priv_der)   # secp256k1 scalar
    eth_address = address(secp256k1(eth_priv))

Why derive instead of reusing the cert key: the cert is secp384r1 (P-384) and
Ethereum signing requires secp256k1, so a P-384 key can never sign a
transaction. Deriving from the identity key means the wallet inherits the
identity's security properties automatically:

  * mainnet — identity key is the CAS-provisioned secret, so the wallet is
    enclave-only and stable across every node running this MRENCLAVE.
  * testnet — identity key is derived from MR_SIGNER/MR_ENCLAVE, both PUBLIC,
    so the wallet is public too. Functional testing only; is_secret_identity()
    reports False and callers must warn loudly (RFC §7).

DOMAIN_SEP keeps this key from colliding with any other use of the identity
key; a second separator derives the state-encryption key in a later phase.

SECURITY: nothing here may serialize, log or return the private key. The only
value that ever leaves the enclave is the ADDRESS (RFC §5.2) — that is what
makes the wallet fundable and readable without weakening secrecy.
"""

import hashlib

DOMAIN_SEP = b"ethernity-cloud/esr-wallet/v1"

# secp256k1 group order; a valid private scalar is in [1, N-1].
_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def _keccak256(data: bytes) -> bytes:
    """Keccak-256 (Ethereum's hash), not SHA3-256."""
    try:
        from Crypto.Hash import keccak  # pycryptodome

        h = keccak.new(digest_bits=256)
        h.update(data)
        return h.digest()
    except ImportError:
        from eth_hash.auto import keccak as eth_keccak  # web3 dependency

        return eth_keccak(data)


def derive_wallet_private_key(identity_priv_der: bytes) -> bytes:
    """32-byte secp256k1 private key derived from the enclave identity key.

    Kept private to this module's callers; never log or transmit the result.
    """
    if not identity_priv_der:
        raise ValueError("empty enclave identity key; cannot derive ESR wallet")
    digest = _keccak256(DOMAIN_SEP + identity_priv_der)
    # Astronomically unlikely, but a scalar of 0 or >= N is invalid: re-hash
    # until valid so derivation is total rather than probabilistically broken.
    while True:
        scalar = int.from_bytes(digest, "big")
        if 0 < scalar < _SECP256K1_N:
            return digest
        digest = _keccak256(DOMAIN_SEP + digest)


def derive_wallet_address(identity_priv_der: bytes) -> str:
    """Checksummed 0x address of the enclave's ESR wallet (safe to publish)."""
    priv = derive_wallet_private_key(identity_priv_der)
    try:
        from eth_keys import keys

        return keys.PrivateKey(priv).public_key.to_checksum_address()
    except ImportError:
        # eth_account ships with the runner dependency chain and is always
        # present in the enclave image; use it when eth_keys is unavailable.
        from eth_account import Account

        return Account.from_key(priv).address


def is_secret_identity(network_type: str) -> bool:
    """True only when the identity key is genuinely enclave-only.

    Mainnet provisions the key through CAS over the attested channel. Testnet
    self-signs from public measurements, so the key — and therefore the wallet
    — is reproducible by anyone (RFC §7).
    """
    return str(network_type).strip().lower() == "mainnet"


INSECURE_IDENTITY_WARNING = (
    "[TESTNET-INSECURE] The enclave identity key on this network is derived "
    "from public measurements (MR_SIGNER/MR_ENCLAVE), so the ESR wallet's "
    "private key is reproducible by anyone. Do not fund it with real value and "
    "do not rely on it for production trust."
)
