"""Smoke: sessions + invites + membership-driven policy (toy embedder, FakeRoom, MockProvider).
create -> invite -> accept -> member accepted; expired / reused / tampered / non-enrolled codes
rejected; revoke mid-session stops forwarding and kicks; isolation forwards only the owner."""

import asyncio
import logging
import tempfile
import time
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet

from voiceroom.adapters import MockProvider
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.matcher import Match, Matcher
from voiceroom.policy import UNKNOWN_LABEL, Verdict
from voiceroom.room import RoomPipeline
from voiceroom.rooms import FakeParticipant, FakeRoom, RoomAgent
from voiceroom.sessions import InviteError, SessionError, SessionPolicy, SessionStore, SessionSync
from voiceroom.store import SignatureStore
from voiceroom.vad import EnergyVAD

SR = 16000
corpus = SynthCorpus(4, seed=1, utts_per_speaker=12)
alice, bob, carol, dave = corpus.speakers()
emb = get_embedder("toy")
enroll_utts, held = split_utterances(corpus, [alice, bob, carol], n_enroll=6)
sigs = {s: enroll(s, np.concatenate([u.load() for u in enroll_utts[s]]), emb) for s in enroll_utts}
cal = Matcher(list(sigs.values()), threshold=0.0)
gen, imp = [], []
for s in sigs:
    for u in held[s]:
        sc = cal.identify_embedding(emb.embed(u.load()[:SR])).scores
        gen.append(sc[s])
        imp += [v for k, v in sc.items() if k != s]
threshold = (float(np.percentile(gen, 5)) + float(np.percentile(imp, 95))) / 2

tmp = Path(tempfile.mkdtemp())
KEY = Fernet.generate_key()
sigstore = SignatureStore(tmp, KEY)
for s in (alice, bob):
    sigstore.save(sigs[s], {"given": True})
sigstore.save(sigs[carol], {})  # enrolled but never consented
clock = [time.time()]
store = SessionStore(tmp, KEY, sigstore, now=lambda: clock[0])


def expect(exc, fn, *a):
    try:
        fn(*a)
    except exc:
        return
    raise AssertionError(f"{fn.__name__} should have raised {exc.__name__}")


# a log handler that must never see a code or token
logged: list[str] = []


class Grab(logging.Handler):
    def emit(self, record):
        logged.append(record.getMessage())


logging.getLogger().addHandler(Grab())
logging.getLogger().setLevel(logging.DEBUG)

# ---- 1. invites -------------------------------------------------------------------------
sess = store.create_session(alice, "collaboration")
assert sess.members == (alice,) and sess.mode == "collaboration" and sess.room
expect(SessionError, store.create_session, dave)  # owner must be enrolled
expect(SessionError, store.invite, sess, dave)  # not enrolled
expect(SessionError, store.invite, sess, carol)  # no consent
code = store.invite(sess, bob)
for bad in (
    code[:-2] + ("AA" if not code.endswith("AA") else "BB"),  # tampered mac
    code.replace(".", ".X", 1),  # tampered payload
    "vr1.junk",
    "",
):
    expect(InviteError, store.accept, bad)
assert store.get(sess.id).members == (alice,)
late = store.invite(sess, bob, ttl_s=10)
clock[0] += 11
expect(InviteError, store.accept, late)  # expired
clock[0] -= 11
joined = store.accept(code)
assert joined.members == (alice, bob), joined
expect(InviteError, store.accept, code)  # single-use
# revoked invite
assert [s.id for s in store.list()] == [sess.id]
raw = next(tmp.glob("*.session")).read_bytes()
assert alice.encode() not in raw and sess.id.encode() not in raw  # encrypted at rest
store_other = SessionStore(tmp, Fernet.generate_key())
expect(Exception, store_other.list)  # wrong key cannot read

# join token (needs the livekit extra)
try:
    import jwt  # noqa: F401
    from livekit import api  # noqa: F401

    tok = store.join_token(sess, bob, "ws://127.0.0.1:7880")
    claims = jwt.decode(tok, options={"verify_signature": False})
    assert claims["sub"] == bob and claims["video"]["room"] == sess.room, claims
    assert claims["attributes"] == {"voiceroom_user": bob}, claims
    expect(SessionError, store.join_token, sess, dave, "ws://127.0.0.1:7880")
except ImportError:
    print("note: livekit extra missing, join_token not checked")


# ---- rooms ------------------------------------------------------------------------------
def utt(spk: str, i: int) -> np.ndarray:
    return held[spk][i].load()[: int(2.5 * SR)]


def track(spk: str, idxs: list[int], gap: float) -> np.ndarray:
    parts = []
    for i in idxs:
        parts += [np.zeros(int(gap * SR), np.float32), utt(spk, i)]
    return np.concatenate(parts + [np.zeros(SR, np.float32)]).astype(np.float32)


async def run_room(session, tracks: dict[str, np.ndarray], revoke_after: str | None = None):
    room = FakeRoom([FakeParticipant(f"p-{u}", a, identity=u) for u, a in tracks.items()])
    provider = MockProvider()
    sync_holder = {}
    pipeline = RoomPipeline(
        provider,
        lambda sid, claimed: sync_holder["s"].policy_factory(threshold=threshold, embedder="toy")(
            sid, claimed
        ),
        Matcher(list(sigs.values()), threshold=threshold, embedder=emb),
        vad_factory=EnergyVAD, max_pending=1000, max_inflight_identify=1000, isolation="thread",
    )  # fmt: skip
    agent = RoomAgent(room, pipeline, provider)
    sync = sync_holder["s"] = SessionSync(store, session.id, pipeline, room.kick, agent=agent)
    snap: list[int] = []
    alive: list[bool] = []

    async def watcher() -> None:
        while True:
            if revoke_after and any(t.speaker_label == revoke_after for t in provider.turns):
                store.revoke(session, revoke_after)
                alive.append(f"p-{revoke_after}" in pipeline.sources)
                snap.append(sum(t.speaker_label == revoke_after for t in provider.turns))
                return
            await asyncio.sleep(0)

    async def syncer() -> None:
        while True:
            await sync.sync_once()
            await asyncio.sleep(0)

    bg = [asyncio.create_task(watcher()), asyncio.create_task(syncer())]
    try:
        await asyncio.wait_for(agent.run(), 90)
    finally:
        for t in bg:
            t.cancel()
    return provider, pipeline, sync, room, snap, alive


def labels(provider) -> list[str]:
    return [t.speaker_label for t in provider.turns]


async def main() -> None:
    # 2. collaboration: owner and the accepted member are both forwarded
    prov, pipe, *_ = await run_room(
        joined, {alice: track(alice, [0, 1], 1.0), bob: track(bob, [0, 1], 1.0)}
    )
    assert set(labels(prov)) == {alice, bob}, labels(prov)

    # 3. revoke mid-session: bob's later turns are not forwarded and bob is kicked
    prov, pipe, sync, room, snap, alive = await run_room(
        joined,
        {alice: track(alice, [0, 1, 2], 6.0), bob: track(bob, [0, 1, 2, 3], 6.0)},
        revoke_after=bob,
    )
    assert snap and snap[0] >= 1, snap
    assert labels(prov).count(bob) == snap[0], (labels(prov), snap)  # nothing after revoke
    assert labels(prov).count(alice) >= 2, labels(prov)
    if alive[0]:  # still streaming at revoke time (else it had already left by itself)
        assert f"p-{bob}" in sync.kicked and f"p-{bob}" in room.kicked, (sync.kicked, room.kicked)
    assert store.get(joined.id).members == (alice,)
    expect(SessionError, store.revoke, joined, alice)  # owner cannot be revoked
    expect(InviteError, store.accept, code)  # old code stays dead
    store.accept(store.invite(joined, bob))  # re-invited later: works again
    assert store.get(joined.id).members == (alice, bob)

    # 4. isolation: only the owner is forwarded even with bob a member
    iso = store.create_session(alice, "isolation")
    store.accept(store.invite(iso, bob))
    assert store.get(iso.id).members == (alice, bob)
    prov, pipe, *_ = await run_room(
        iso, {alice: track(alice, [0, 1], 1.0), bob: track(bob, [0, 1], 1.0)}
    )
    assert labels(prov) and set(labels(prov)) == {alice}, labels(prov)
    assert any(r.source_id == f"p-{bob}" and r.verdict is Verdict.REJECT for r in pipe.records)


asyncio.run(main())


# kick path, deterministic: a live source of a revoked member is removed and kicked
async def kick_check() -> None:
    class Src:
        def __init__(self, claimed):
            self.claimed = claimed

    class Pipe:
        sources = {"pa": Src(alice), "pb": Src(bob), "ps": Src(None)}
        removed: list[str] = []

        def remove_source(self, sid):
            self.removed.append(sid)
            self.sources.pop(sid)

    s = store.create_session(alice, "collaboration")
    store.accept(store.invite(s, bob))
    fake, pipe = FakeRoom([]), Pipe()
    sy = SessionSync(store, s.id, pipe, fake.kick)
    await sy.sync_once()
    assert not pipe.removed
    store.revoke(s, bob)
    await sy.sync_once()
    assert pipe.removed == ["pb"] and fake.kicked == ["pb"] and set(pipe.sources) == {"pa", "ps"}


asyncio.run(kick_check())


# roster kick (no audio, transport identity != voice identity) and the shared-device gate
async def roster_check() -> None:
    from voiceroom.rooms import ParticipantJoined

    class Agent:
        participants = {
            "q1": ParticipantJoined("q1", "device-b", {"voiceroom_user": bob}),  # listening only
            "q2": ParticipantJoined("q2", bob),
            "q3": ParticipantJoined("q3", "shared-device"),  # never a member
        }

    class Pipe:
        sources: dict = {}
        deliver_gate = None

        def remove_source(self, sid):
            pass

    s = store.create_session(alice, "collaboration")
    store.accept(store.invite(s, bob))
    fake = FakeRoom([])
    sy = SessionSync(store, s.id, Pipe(), fake.kick, agent=Agent())
    await sy.sync_once()
    assert not fake.kicked
    assert sy._allowed("shared-device", alice)  # shared device, member's voice
    store.revoke(s, bob)
    await sy.sync_once()
    assert sorted(fake.kicked) == ["q1", "q2"], fake.kicked  # not the shared device
    assert dict(zip(fake.kicked, fake.kicked_identities, strict=True)) == {
        "q1": "device-b",
        "q2": bob,
    }, fake.kicked_identities
    assert not sy._allowed(bob, bob) and not sy._allowed(bob, alice)
    assert sy._allowed("shared-device", alice) and not sy._allowed("shared-device", bob)


asyncio.run(roster_check())


# 5. policy level: a revoked member's claimed track is "unknown" (label) / dropped (drop)
def m(sc: dict) -> Match:
    best = max(sc, key=sc.get)
    return Match(best if sc[best] >= threshold else None, sc[best], 0.3, best, sc)


for unknown, want in (("label", UNKNOWN_LABEL), ("drop", None)):
    s = store.create_session(alice, "collaboration", unknown=unknown)
    store.accept(store.invite(s, bob))
    pol = SessionPolicy(store, s.id, bob, threshold=threshold, embedder="toy")
    d = pol.decide(m({alice: 0.0, bob: threshold + 0.3}))
    assert d.verdict is Verdict.ACCEPT and d.label == bob, d
    store.revoke(s, bob)
    d = pol.decide(m({alice: 0.0, bob: threshold + 0.3}))  # picks up the change, no rebuild
    assert (d.label if d.verdict is Verdict.ACCEPT else None) == want, (unknown, d)

assert not any(code in line for line in logged), "invite code logged"
print("sessions ok")
