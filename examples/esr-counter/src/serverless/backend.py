"""ESR end-to-end test payload.

Exercises the whole Enclave State Registry path in a single task:

    read state -> mutate -> encrypt -> compute CID in-enclave
      -> publish blob + CID to SwiftStream -> commit on-chain
      -> node pins and verifies

Every function returns plain data, because ESR state is ENCLAVE-PRIVATE: a
client cannot decrypt it. Anything the caller should see has to be returned
explicitly, which is exactly the intended pattern -- the payload decides what
leaves the enclave.
"""


def esr_address():
    """The enclave's ESR wallet address. Fund this to let it commit."""
    from ecld_state import StateRegistry

    return {"wallet": StateRegistry().wallet_address}


def esr_increment(key="e2e-counter"):
    """Increment a counter held in encrypted state; return before/after.

    Running this twice should show the counter advancing and the version
    incrementing -- which is the proof that state actually persisted on-chain
    and was read back correctly.
    """
    from ecld_state import StateRegistry

    state = StateRegistry()
    before = state.get(key)
    after = state.commit(key, lambda s: {**s, "n": s.get("n", 0) + 1})
    return {
        "wallet": state.wallet_address,
        "key": key,
        "before": before,
        "after": after,
        "version": state.get_version(key),
    }


def esr_read(key="e2e-counter"):
    """Read state without writing. Returns {} when nothing was ever committed."""
    from ecld_state import StateRegistry

    state = StateRegistry()
    return {
        "key": key,
        "state": state.get(key),
        "version": state.get_version(key),
    }


def esr_selftest():
    """Check the pieces that do not need gas, so failures are easy to localise.

    Verifies the module is wired, the wallet derives, encryption round-trips,
    and the in-enclave CID matches what IPFS would produce for the same bytes.
    A failure here means the enclave build is wrong; a failure only in
    esr_increment means the chain/funding side is.
    """
    import json

    import ecld_state
    from ecld_state import StateRegistry

    results = {}
    try:
        results["wallet"] = StateRegistry().wallet_address
    except Exception as e:
        results["wallet_error"] = str(e)

    try:
        blob = ecld_state._encrypt(json.dumps({"probe": 1}).encode())
        results["encrypt_roundtrip"] = json.loads(
            ecld_state._decrypt(blob).decode()
        ) == {"probe": 1}
        results["cid_of_blob"] = ecld_state.cidv1_raw(blob)
    except Exception as e:
        results["crypto_error"] = str(e)

    # Known vector: this must equal what `ipfs add --cid-version=1 --raw-leaves`
    # returns for the same bytes.
    results["cid_known_vector_ok"] = (
        ecld_state.cidv1_raw(b"hello-esr\n")
        == "bafkreiadxa2sf2hlf3r4qchmh4g5vtpcx6i7smlh4po4yplefbpbcyf23m"
    )
    return results
