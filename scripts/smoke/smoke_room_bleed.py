"""Smoke: cross-talk bleed between co-located mics (toy embedder, on_mismatch="relabel" so a copy
would otherwise be committed twice). -12 dB bleed commits once; true simultaneous speech on own
tracks commits twice; with no bleed the detector changes nothing."""

import asyncio

import numpy as np

from voiceroom.adapters import MockProvider
from voiceroom.audio import iter_frames
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import bleed_tracks, split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.matcher import Matcher
from voiceroom.policy import TrackPolicy, Verdict
from voiceroom.room import RoomPipeline
from voiceroom.synth import add_bleed
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


def solo(parts: list[tuple[str, int, float, float]]) -> np.ndarray:
    """parts: (speaker, utterance idx, start s, max length s) on one clean mic."""
    end = max(t + min(len(held[s][i].load()), int(m * SR)) / SR for s, i, t, m in parts) + 1.0
    audio = np.zeros(int(end * SR) // FRAME_SIZE * FRAME_SIZE + FRAME_SIZE, np.float32)
    for s, i, t, m in parts:
        a = held[s][i].load()[: int(m * SR)]
        audio[int(t * SR) : int(t * SR) + len(a)] = a
    return audio


async def play(audio: np.ndarray, pace: float = 0.0):
    for f in iter_frames(audio, FRAME_SIZE):
        yield f
        await asyncio.sleep(pace)


async def run(
    audio: dict[str, np.ndarray],
    who: dict[str, str],
    bleed: bool,
    pace: float = 0.0,
    provider=None,
    **kw,
):
    provider = provider or MockProvider()
    room = RoomPipeline(
        provider,
        lambda sid, claimed: TrackPolicy(
            "verified", members, claimed, on_mismatch="relabel",
            threshold=threshold, verify_threshold=verify, window_drop=0.05, **kw,
        ),
        Matcher(sigs, threshold=threshold, embedder=emb),
        vad_factory=EnergyVAD,
        max_pending=1000,
        max_inflight_identify=1000,
        isolation="thread",
        bleed=bleed,
    )  # fmt: skip
    for sid, a in audio.items():
        room.add_source(sid, play(a, pace), who[sid])
    await room.run(stop_when_idle=True)
    return list(room.records), provider


def commits(recs) -> list[tuple[str, str | None]]:
    return [(r.source_id, r.speaker) for r in recs if r.verdict is Verdict.ACCEPT]


class FlakyProvider(MockProvider):
    failed = False

    async def send_turn(self, audio16k, speaker_label):
        if not self.failed:
            self.failed = True
            raise RuntimeError("transient")
        await super().send_turn(audio16k, speaker_label)


async def main() -> None:
    who = {"a": alice, "b": bob}
    # (unknown="label": a noisy copy that matches nobody would be committed as Unknown)
    # sequential speech, each speaker on their own mic; the other mic hears a copy
    clean = {
        "a": solo([(alice, 0, 0.5, 2.5), (alice, 1, 7.0, 2.5)]),
        "b": solo([(bob, 0, 3.5, 2.5), (bob, 1, 10.0, 2.5)]),
    }
    n = max(map(len, clean.values()))
    clean = {k: np.pad(v, (0, n - len(v))) for k, v in clean.items()}
    for db, delay in ((-12.0, 15.0), (-18.0, 5.0), (-10.0, 40.0)):
        bl = bleed_tracks(clean, level_db=db, delay_ms=delay)
        recs, prov = await run(bl, who, bleed=True, unknown="label")
        got = commits(recs)
        assert got == [("a", alice), ("b", bob), ("a", alice), ("b", bob)], (db, got)
        assert len(prov.turns) == 4, recs
        if db == -12.0:  # the copies are heard as turns and dropped by the detector
            assert sum(r.bleed_of is not None for r in recs) >= 2, recs
    # without the detector the same audio is duplicated (so the check above bites)
    bl = bleed_tracks(clean, level_db=-12.0, delay_ms=15.0)
    recs, prov = await run(bl, who, bleed=False, unknown="label")
    assert len(prov.turns) > 4, len(prov.turns)

    # true simultaneous speech, both on their own mics
    sim = {"a": solo([(alice, 2, 0.5, 3.0)]), "b": solo([(bob, 2, 1.0, 3.0)])}
    n = max(map(len, sim.values()))
    sim = {k: np.pad(v, (0, n - len(v))) for k, v in sim.items()}
    recs, prov = await run(sim, who, bleed=True)
    assert sorted(commits(recs)) == [("a", alice), ("b", bob)], recs
    assert not any(r.bleed_of for r in recs), recs

    # bleed off in the audio: detector on == detector off (overlapping owners, mux-like)
    mux = {"a": clean["a"], "b": solo([(bob, 0, 1.5, 2.5), (bob, 1, 5.5, 2.5)])}
    n = max(map(len, mux.values()))
    mux = {k: np.pad(v, (0, n - len(v))) for k, v in mux.items()}
    r_on, p_on = await run(mux, who, bleed=True)
    r_off, p_off = await run(mux, who, bleed=False)
    key = lambda rs: [(r.source_id, r.verdict, r.speaker, round(r.t_end, 3)) for r in rs]  # noqa: E731
    assert key(r_on) == key(r_off) and len(p_on.turns) == len(p_off.turns) == 4, (r_on, r_off)

    # paced streaming; the copy (on b) ends 350 ms before its original (a), so it is decided first
    one = {"a": solo([(alice, 0, 0.5, 2.5)]), "b": np.zeros_like(solo([(alice, 0, 0.5, 2.5)]))}
    cut = one["a"].copy()
    cut[len(cut) - int(0.7 * SR) :] = 0
    last = np.nonzero(one["a"])[0][-1]
    cut[last - int(0.35 * SR) :] = 0
    one["b"] = add_bleed(one["b"], cut, -12.0, 15.0)
    recs, prov = await run(one, who, bleed=True, pace=0.002, unknown="label")
    assert [r.source_id for r in recs if r.verdict is Verdict.ACCEPT] == ["a"], recs
    recs, prov = await run(one, who, bleed=False, pace=0.002, unknown="label")
    assert len(prov.turns) == 2, "paced copy should duplicate without the detector"

    # the copy stops at ~1.2 s, while Alice's turn is still open (default waits)
    short = add_bleed(np.zeros_like(one["a"]), one["a"][: int(1.2 * SR)], -12.0, 15.0)
    recs, prov = await run(
        {"a": one["a"], "b": short}, who, bleed=True, pace=0.002, unknown="label"
    )
    assert len(prov.turns) == 1 and [r.bleed_of for r in recs if r.source_id == "b"] == ["a"], recs
    # ... and Alice's first send fails: the held copy is delivered instead
    recs, prov = await run(
        {"a": one["a"], "b": short}, who, bleed=True, pace=0.002, unknown="label",
        provider=FlakyProvider(),
    )  # fmt: skip
    assert [r.error for r in recs if r.source_id == "a"] == ["send_error"], recs
    assert len(prov.turns) == 1 and not any(r.bleed_of for r in recs), recs

    # same speaker, different speech: Alice live on a, other Alice speech on b's mic (synthetic
    # utterances of one speaker are near-identical waveforms, so shuffle 0.3 s chunks of one)
    x = held[alice][1].load()[: int(2.5 * SR)]
    chunks = [x[i : i + 4800] for i in range(0, len(x) - 4799, 4800)]
    older = np.concatenate([chunks[i] for i in np.random.default_rng(3).permutation(len(chunks))])
    base = solo([(alice, 0, 0.5, 2.5)])
    same = {"a": base, "b": np.zeros_like(base)}
    same["b"][int(0.8 * SR) : int(0.8 * SR) + len(older)] = older * 0.2
    recs, prov = await run(same, who, bleed=False)
    assert len(prov.turns) == 2, "setup: both are committed without the detector"
    recs, prov = await run(same, who, bleed=True)
    assert len(prov.turns) == 2 and not any(r.bleed_of for r in recs), recs

    # genuine quieter guest on b's mic (independent speech, 0.2 gain) over a: both are committed
    guest = {"a": solo([(alice, 0, 0.5, 2.5)]), "b": solo([(stranger, 0, 0.8, 2.5)]) * 0.2}
    n = max(map(len, guest.values()))
    guest = {k: np.pad(v, (0, n - len(v))) for k, v in guest.items()}
    recs, prov = await run(guest, who, bleed=True, unknown="label")
    assert sorted(r.source_id for r in recs if r.verdict is Verdict.ACCEPT) == ["a", "b"], recs

    # paced: a quieter stranger over Alice's long speech is committed without waiting for her
    long_a = solo([(alice, 0, 0.5, 3.0)])
    gst = np.zeros_like(long_a)
    g = held[stranger][0].load()[: int(1.0 * SR)] * 0.2
    gst[int(0.8 * SR) : int(0.8 * SR) + len(g)] = g
    recs, prov = await run({"a": long_a, "b": gst}, who, bleed=True, pace=0.032, unknown="label")
    rb = [r for r in recs if r.source_id == "b"]
    assert len(rb) == 1 and rb[0].verdict is Verdict.ACCEPT and rb[0].latency_s < 0.7, recs
    print("room bleed ok")


asyncio.run(main())
