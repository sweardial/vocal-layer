"""Smoke: TrackPolicy through RoomPipeline (toy embedder). Verified tracks accept the owner
(incl. 1-2 s turns), relabel/drop another member, drop/label a stranger; shared mic attributes
by voice."""

import asyncio

import numpy as np

from voiceroom.adapters import MockProvider
from voiceroom.audio import iter_frames
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.matcher import Matcher
from voiceroom.policy import UNKNOWN_LABEL, TrackPolicy, Verdict
from voiceroom.room import RoomPipeline
from voiceroom.vad import FRAME_SIZE, EnergyVAD

SR = 16000
corpus = SynthCorpus(4, seed=1, utts_per_speaker=12)
spks = corpus.speakers()
alice, bob, carol, stranger = spks  # the stranger is never enrolled
members = [alice, bob, carol]
emb = get_embedder("toy")
enroll_utts, held = split_utterances(corpus, spks, n_enroll=6)
sigs = [enroll(s, np.concatenate([u.load() for u in enroll_utts[s]]), emb) for s in members]
cal = Matcher(sigs, threshold=0.0)
gen, imp = [], []
for s in members:
    for u in held[s]:
        sc = cal.identify_embedding(emb.embed(u.load()[:SR])).scores
        gen.append(sc[s])
        imp += [v for k, v in sc.items() if k != s]
threshold = (float(np.percentile(gen, 5)) + float(np.percentile(imp, 95))) / 2
verify = threshold - 0.02  # explicit: the toy embedder has no calibrated config value


def track(parts: list[tuple[str, int, float, float]]) -> np.ndarray:
    """parts: (speaker, utterance idx, start s, max length s)."""
    end = max(t + min(len(held[s][i].load()), int(m * SR)) / SR for s, i, t, m in parts) + 1.0
    audio = np.zeros(int(end * SR) // FRAME_SIZE * FRAME_SIZE + FRAME_SIZE, np.float32)
    for s, i, t, m in parts:
        a = held[s][i].load()[: int(m * SR)]
        audio[int(t * SR) : int(t * SR) + len(a)] = a
    return audio


async def play(audio: np.ndarray):
    for f in iter_frames(audio, FRAME_SIZE):
        yield f
        await asyncio.sleep(0)


async def run(tracks: dict, **policy_kw) -> tuple[list, MockProvider]:
    """tracks: sid -> (claimed, parts). Returns the room records and the provider."""
    provider = MockProvider()
    room = RoomPipeline(
        provider,
        lambda sid, claimed: TrackPolicy(
            "shared" if claimed is None else "verified", members, claimed,
            threshold=threshold, verify_threshold=verify, window_drop=0.05, **policy_kw,
        ),
        Matcher(sigs, threshold=threshold, embedder=emb),
        vad_factory=EnergyVAD,
        max_pending=1000,
        max_inflight_identify=1000,
        isolation="thread",
    )  # fmt: skip
    for sid, (claimed, parts) in tracks.items():
        room.add_source(sid, play(track(parts)), claimed)
    await room.run(stop_when_idle=True)
    return list(room.records), provider


async def main() -> None:
    # 1. owner on their own track, incl. 1.0-2.0 s turns
    own = [(alice, 0, 0.5, 1.0), (alice, 1, 3.0, 1.5), (alice, 2, 6.0, 2.0), (alice, 3, 9.0, 4.0)]
    recs, prov = await run({"a": (alice, own)})
    assert len(recs) == 4 and all(r.verdict is Verdict.ACCEPT for r in recs), recs
    assert [t.speaker_label for t in prov.turns] == [alice] * 4, prov.turns

    # 2. bob speaks on alice's track: dropped, or relabelled as bob
    swap = [(alice, 0, 0.5, 2.5), (bob, 0, 4.0, 2.5), (alice, 1, 8.0, 2.5)]
    recs, prov = await run({"a": (alice, swap)}, on_mismatch="drop")
    assert [t.speaker_label for t in prov.turns] == [alice, alice], prov.turns
    assert [r.verdict for r in recs].count(Verdict.REJECT) == 1, recs
    recs, prov = await run({"a": (alice, swap)}, on_mismatch="relabel")
    assert [t.speaker_label for t in prov.turns] == [alice, bob, alice], prov.turns

    # 3. a stranger on alice's track: dropped, or labelled Unknown
    strange = [(alice, 0, 0.5, 2.5), (stranger, 0, 4.0, 2.5)]
    recs, prov = await run({"a": (alice, strange)}, unknown="drop")
    assert [t.speaker_label for t in prov.turns] == [alice], prov.turns
    recs, prov = await run({"a": (alice, strange)}, unknown="label")
    assert [t.speaker_label for t in prov.turns] == [alice, UNKNOWN_LABEL], prov.turns

    # 4. shared mic (no claimed identity), 2 members: attributed by voice
    shared = [(alice, 0, 0.5, 2.5), (bob, 0, 4.0, 2.5), (alice, 1, 8.0, 2.5)]
    recs, prov = await run({"s": (None, shared)})
    assert [t.speaker_label for t in prov.turns] == [alice, bob, alice], prov.turns

    # 5. policy-level: the 1:1 threshold is looser than identification; default binding works
    from voiceroom.matcher import Match

    def m(sc: dict, thr: float) -> Match:
        best = max(sc, key=sc.get)
        return Match(best if sc[best] >= thr else None, sc[best], 0.3, best, sc)

    for scale, cohort_thr in ((1.0, 0.35), (10.0, 2.88)):
        pol = TrackPolicy("verified", ["alice", "bob"], "alice")
        pol.bind_matcher(type("M", (), {"threshold": cohort_thr, "score_scale": scale,
                                        "cohort": None, "embedder": None})())  # fmt: skip
        lo = cohort_thr - 0.03 * scale  # between the 1:1 and the identification threshold
        assert pol.decide(m({"alice": lo, "bob": 0.0}, cohort_thr)).verdict is Verdict.ACCEPT
        assert pol.decide(m({"alice": cohort_thr - 0.2 * scale, "bob": 0}, cohort_thr)).verdict is (
            Verdict.REJECT
        )
        # a confident non-member (revoked) on alice's track is never accepted as alice
        d = pol.decide(m({"alice": lo, "mallory": cohort_thr + 0.2 * scale}, cohort_thr))
        assert d.verdict is Verdict.REJECT, d
    print("room identity ok")


asyncio.run(main())
