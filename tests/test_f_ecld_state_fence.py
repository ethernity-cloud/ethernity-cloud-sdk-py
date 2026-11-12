"""The fence in the state module (PHASE-D-PLAN.md F.4) over esr_local's
chain, placed around tests/spec/session-fence.json's toBlock bound: a key
with commits before and after the bound is re-committed at its state as of
the bound (the later not taken), a key of another holder is left as its
writer committed it, a key the holder owns under another reservation and a
key the holder may write as a member are re-committed, a key first
committed after the bound is outside; each re-commit is re-wrapped with
nonce getNonce + 1 as a fresh CID, signed on the key's current version and
relay nonce, staged at once as esr.commit.<key16>.<relayNonce>.json outside
the order ledger, re-signed after a landing moved the key until it lands,
one landing record per key; the resumption point reads as of the bound.
The signal: session.fence.securelock verified under the trustedzone's key
over "<challenge>:<order_id>:session.fence:" + plaintext with the fixture's
signature, a tampered plaintext refused, kind none fencing nothing, an
unreserved order (session.input.0 without a fence) waiting for nothing."""
import io
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization

from tests.f_session_support import BUCKET, CHALLENGE, ORDER_ID, SPEC, FakeApp, content, load_modules, names

FENCE = json.loads((SPEC / "session-fence.json").read_text(encoding="utf-8"))
TZ_KEY = json.loads((SPEC / "capability-record.json").read_text(encoding="utf-8"))["trustedzoneKey"]
BOUND = FENCE["examples"]["toBlock"]["bound"]
H = "0x" + "11" * 20
H2 = "0x" + "22" * 20
IDENTITY = b"ecld-local-test-identity-key-0001"


def _pub_hex(pem):
    key = serialization.load_pem_public_key(pem.encode("utf-8"))
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)[1:].hex()


@pytest.fixture
def chain(monkeypatch):
    m = load_modules(monkeypatch)
    ecld_state, esr_local = m["ecld_state"], m["esr_local"]
    monkeypatch.setattr(ecld_state, "LOG_RANGE_BLOCKS", 7)
    monkeypatch.setattr(ecld_state, "ENTRIES_PAGE", 2)
    monkeypatch.setattr(ecld_state, "FENCE_POLL_SECONDS", 0.01)
    monkeypatch.setattr(ecld_state, "STATE_STAGE_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(ecld_state, "STATE_STAGE_POLL_SECONDS", 0.01)
    swift = esr_local.MemSwift()
    reg = esr_local.MemRegistry()
    web3 = esr_local.MemWeb3(reg)

    def task(caller):
        ecld_state.configure(identity_priv=IDENTITY, swift_stream_service=swift, bucket=BUCKET,
                             contract_address="0xLOCAL", web3=web3, caller=caller)
        return ecld_state.StateRegistry()

    ecld_state.set_local_commit_apply(
        lambda enclave, kh, cid, ev, nonce=0: reg.apply_commit(enclave, kh, cid, ev, nonce))
    wallet = task(H).wallet_address

    def commit_at(block, caller, key, state):
        """A commit landed at `block`, its object staged by the node under its CID."""
        reg.land_at(block)
        task(caller).commit(key, lambda s: state)
        cid = reg.state(wallet, ecld_state._key_hash(key))[0]
        swift.put_file_content(BUCKET, f"state.{cid}.enc", "", io.BytesIO(content(swift, f"state.{key}.enc")))
        return cid

    cids = {}
    cids["k1v1"] = commit_at(BOUND - 61, H, "k1", {"n": 1})
    commit_at(BOUND - 51, H, "k3", {"reservation": "other"})
    commit_at(BOUND - 46, H2, "k2", {"holder": "other"})
    commit_at(BOUND - 45, H2, "k5", {"shared": True})
    reg.land_at(BOUND - 44)
    task(H2)
    ecld_state.esr_grant("k5", H, "write")
    cid5 = reg.state(wallet, ecld_state._key_hash("k5"))[0]
    swift.put_file_content(BUCKET, f"state.{cid5}.enc", "", io.BytesIO(content(swift, "state.k5.enc")))
    cids["k1v2"] = commit_at(BOUND - 11, H, "k1", {"n": 2})
    cids["k1v3"] = commit_at(BOUND + 9, H, "k1", {"n": 3})
    commit_at(BOUND + 19, H, "k4", {"late": True})
    reg.land_at(BOUND + 29)
    kh = {k: ecld_state._key_hash(k) for k in ("k1", "k2", "k3", "k4", "k5")}
    before = {k: reg.state(wallet, kh[k]) for k in kh}

    moved = {"k1": False}

    def landing(enclave, key_hash, cid, expected_version, nonce=0):
        """The node's relay, one block later; the first fence attempt on k1
        is preceded by a predecessor's late commit, so that attempt reverts."""
        reg.land_at(reg.block_number + 1)
        if key_hash == kh["k1"] and not moved["k1"]:
            moved["k1"] = True
            reg.apply_commit(enclave, key_hash, "bafkreilatecommitofthepredecessorxxxxxxxxxxxxxxxxxxxxxxxx", expected_version, 0)
            return
        reg.apply_commit(enclave, key_hash, cid, expected_version, nonce)

    ecld_state.set_local_commit_apply(landing)
    return SimpleNamespace(ecld_state=ecld_state, swift=swift, reg=reg, web3=web3, task=task, wallet=wallet,
                           kh=kh, cids=cids, before=before, m=m)


def _blob(chain, key):
    kh16 = chain.kh[key].hex()[:16]
    return json.loads(chain.ecld_state._decrypt(content(chain.swift, f"state.fence.{kh16}.enc")))


def test_the_holders_keys_are_re_committed_as_of_the_bound(chain):
    ledger = chain.task(H).fence(BOUND)
    assert sorted(r["keyHash"] for r in ledger) == sorted("0x" + chain.kh[k].hex() for k in ("k1", "k3", "k5"))
    assert len(ledger) == 3
    reg, wallet, kh = chain.reg, chain.wallet, chain.kh
    # k1: v1, v2, v3, the predecessor's late v4, the fence's v5 at the state of v2.
    k1 = next(r for r in ledger if r["keyHash"] == "0x" + kh["k1"].hex())
    assert (k1["expectedVersion"], k1["relayNonce"], k1["nonce"], k1["owned"], k1["callerUsed"]) == (4, 4, 5, True, H)
    assert reg.state(wallet, kh["k1"]) == (k1["cid"], 5, 0) and reg.idem_nonce(wallet, kh["k1"]) == 5
    assert k1["cid"] not in chain.cids.values()
    blob = _blob(chain, "k1")
    assert (blob["data"], blob["nonce"], blob["acl"]["owner"], blob["committedBy"]) == ({"n": 2}, 5, H, H)
    assert content(chain.swift, f"state.fence.{kh['k1'].hex()[:16]}.cid") == k1["cid"].encode()
    # k3 and k5 at their only state; k2 and k4 as their writers committed them.
    k3 = next(r for r in ledger if r["keyHash"] == "0x" + kh["k3"].hex())
    assert (k3["expectedVersion"], k3["nonce"]) == (1, 2) and _blob(chain, "k3")["data"] == {"reservation": "other"}
    k5 = next(r for r in ledger if r["keyHash"] == "0x" + kh["k5"].hex())
    assert (k5["expectedVersion"], k5["nonce"], k5["owned"]) == (2, 3, True)
    assert _blob(chain, "k5")["acl"]["owner"] == H2 and _blob(chain, "k5")["acl"]["write"] == [H]
    assert reg.state(wallet, kh["k2"]) == chain.before["k2"] and reg.state(wallet, kh["k4"]) == chain.before["k4"]
    assert reg.state(wallet, kh["k3"])[1] == 2 and reg.state(wallet, kh["k5"])[1] == 3


def test_each_attempt_is_a_staged_file_and_the_ledger_keeps_the_landing_record(chain):
    before = names(chain.swift, "esr.commit.")
    authorizations = content(chain.swift, "esr.authorizations.json")
    ledger = chain.task(H).fence(BOUND)
    k1_16 = chain.kh["k1"].hex()[:16]
    staged = [n for n in names(chain.swift, "esr.commit.") if n not in before]
    assert sorted(staged) == sorted([f"esr.commit.{k1_16}.3.json", f"esr.commit.{k1_16}.4.json",
                                     f"esr.commit.{chain.kh['k3'].hex()[:16]}.1.json",
                                     f"esr.commit.{chain.kh['k5'].hex()[:16]}.2.json"])
    first = json.loads(content(chain.swift, f"esr.commit.{k1_16}.3.json"))
    landed = json.loads(content(chain.swift, f"esr.commit.{k1_16}.4.json"))
    assert (first["expectedVersion"], first["relayNonce"], first["nonce"]) == (3, 3, 4)
    assert landed == next(r for r in ledger if r["keyHash"] == "0x" + chain.kh["k1"].hex())
    assert content(chain.swift, "esr.authorizations.json") == authorizations
    assert chain.ecld_state.ledger_attestation()["commits"] == []
    assert chain.ecld_state._task_relay_nonces[chain.kh["k1"].hex()] == 5


def test_the_resumption_point_reads_as_of_the_bound(chain):
    registry = chain.task(H)
    registry.fence(BOUND)
    assert registry.get("k1") == {"n": 2}
    assert registry._read_container("k1")[2] == 5
    assert registry.get("k3") == {"reservation": "other"}
    assert chain.task(H).get("k4") == {"late": True}
    registry = chain.task(H)
    registry.fence(BOUND)
    registry.commit("k1", lambda s: {**s, "n": s["n"] + 1})
    assert registry.get("k1") == {"n": 3} and chain.reg.state(chain.wallet, chain.kh["k1"])[1] == 7


def test_without_a_caller_or_a_landing_nothing_is_fenced(chain, monkeypatch):
    assert chain.task(None).fence(BOUND) == []
    assert chain.reg.state(chain.wallet, chain.kh["k1"]) == chain.before["k1"]
    monkeypatch.setattr(chain.ecld_state, "FENCE_LANDING_WAIT_SECONDS", 0.1)
    chain.ecld_state.set_local_commit_apply(lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="did not land"):
        chain.task(H).fence(BOUND)


class _VerifyingApp(FakeApp):
    """FakeApp verifying a staged object's signature with the securelock's
    verifier under the fixture's trustedzone key."""

    def __init__(self, swift, crypto, **kw):
        super().__init__(swift, **kw)
        self.crypto = crypto
        self.signed = {}

    def stage_signed(self, name, text, sig_hex):
        self.signed[name] = (text, sig_hex)
        self.stage(name, text)

    def get_and_verify_trustedzone_object(self, name, signed_prefix=""):
        if name not in self.signed:
            return super().get_and_verify_trustedzone_object(name, signed_prefix)
        self.log.append(("read", name, signed_prefix))
        text, sig_hex = self.signed[name]
        if not self.crypto.verify_signature(_pub_hex(TZ_KEY["publicKeyPem"]),
                                            (signed_prefix + text).encode("utf-8"), bytes.fromhex(sig_hex)):
            raise ValueError(f"trustedzone signature invalid for {name}")
        return text


def _session(chain):
    import etny_crypto
    chain.task(H)
    app = _VerifyingApp(chain.swift, etny_crypto.etny_crypto, caller=H)
    app.stage("session.config.securelock", json.dumps(
        {"order_id": ORDER_ID, "close_after_seconds": 60, "input_cutoff_seconds": 60, "max_messages": 256}))
    return app, chain.m["etny_session_exec"].SecureLockSession(app)


def test_the_signal_is_verified_under_the_trustedzone_key_and_fences_at_its_bound(chain, monkeypatch):
    monkeypatch.setattr(chain.m["etny_session_exec"], "TICK_SECONDS", 0.01)
    example = FENCE["examples"]["toBlock"]
    app, session = _session(chain)
    assert session._session_binding("session.fence") + example["plaintext"] == example["signedText"]
    app.stage_signed(FENCE["objects"]["pynithy"]["fence"], example["plaintext"], example["signatureDerHex"])
    session._fence_before_first_input()
    assert ("read", "session.fence.securelock", f"{CHALLENGE}:{ORDER_ID}:session.fence:") in app.log
    assert chain.reg.state(chain.wallet, chain.kh["k1"])[1] == 5 and _blob(chain, "k1")["data"] == {"n": 2}
    assert chain.reg.state(chain.wallet, chain.kh["k2"]) == chain.before["k2"]


def test_a_tampered_signal_is_refused_and_kind_none_fences_nothing(chain, monkeypatch):
    monkeypatch.setattr(chain.m["etny_session_exec"], "TICK_SECONDS", 0.01)
    example = FENCE["examples"]["toBlock"]
    app, session = _session(chain)
    tampered = example["plaintext"].replace(str(BOUND), str(BOUND + 1))
    app.stage_signed("session.fence.securelock", tampered, example["signatureDerHex"])
    with pytest.raises(ValueError, match="signature invalid"):
        session._fence_before_first_input()
    assert chain.reg.state(chain.wallet, chain.kh["k1"]) == chain.before["k1"]
    none = FENCE["examples"]["none"]
    app, session = _session(chain)
    app.stage_signed("session.fence.securelock", none["plaintext"], none["signatureDerHex"])
    session._fence_before_first_input()
    assert chain.reg.state(chain.wallet, chain.kh["k1"]) == chain.before["k1"]
    assert chain.ecld_state._fence_points == {}


def test_an_unreserved_order_waits_for_no_fence(chain, monkeypatch):
    monkeypatch.setattr(chain.m["etny_session_exec"], "TICK_SECONDS", 0.01)
    app, session = _session(chain)
    app.stage("session.input.0.securelock", "hello")
    session._fence_before_first_input()
    assert [entry for entry in app.log if entry[0] == "read" and entry[1].startswith("session.fence")] == []
    assert chain.reg.state(chain.wallet, chain.kh["k1"]) == chain.before["k1"]
