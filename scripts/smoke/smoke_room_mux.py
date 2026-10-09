"""Smoke: 3 synthetic sources (overlapping speech, one added mid-run, one removed mid-turn)
through RoomPipeline into one MockProvider; every accepted turn committed once, in end order."""

import asyncio

import numpy as np

from voiceroom.adapters import MockProvider
from voiceroom.audio import iter_frames
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.matcher import Matcher
from voiceroom.policy import IsolationPolicy, Verdict
from voiceroom.room import RoomPipeline
from voiceroom.vad import FRAME_SIZE, EnergyVAD

SR = 16000
corpus = SynthCorpus(3, seed=1, utts_per_speaker=12)
spks = corpus.speakers()
emb = get_embedder("toy")
enroll_utts, held = split_utterances(corpus, spks, n_enroll=6)
sigs = [enroll(s, np.concatenate([u.load() for u in enroll_utts[s]]), emb) for s in spks]
cal = Matcher(sigs, threshold=0.0)
gen, imp = [], []
for s in spks:
    for u in held[s]:
        sc = cal.identify_embedding(emb.embed(u.load()[:SR])).scores
        gen.append(sc[s])
        imp += [v for k, v in sc.items() if k != s]
threshold = (float(np.percentile(gen, 5)) + float(np.percentile(imp, 95))) / 2


def utt(spk: str, i: int, max_s: float = 2.5) -> np.ndarray:
    return held[spk][i].load()[: int(max_s * SR)]


# (source, spk, room start s, utterance index); source "c" joins at room t=5.0 (local = -5.0).
PLAN = [
    ("a", spks[0], 0.5, 0), ("b", spks[1], 1.5, 0), ("a", spks[0], 4.0, 1),
    ("b", spks[1], 5.5, 1), ("c", spks[2], 6.0, 0), ("a", spks[0], 8.0, 2),
    ("c", spks[2], 9.5, 1),
]  # fmt: skip
JOIN_C, CUT_B = 5.0, 7.0  # b is removed at 7.0 s, mid-way through its 5.5 s utterance
offsets = {"a": 0.0, "b": 0.0, "c": JOIN_C}
who = {"a": spks[0], "b": spks[1], "c": spks[2]}


def track(sid: str) -> tuple[np.ndarray, list[tuple[float, float]]]:
    parts = [(t - offsets[sid], utt(spk, i)) for s, spk, t, i in PLAN if s == sid]
    end = max(t + len(a) / SR for t, a in parts) + 1.0
    audio = np.zeros(int(end * SR) // FRAME_SIZE * FRAME_SIZE + FRAME_SIZE, np.float32)
    spans = []
    for t, a in parts:
        audio[int(t * SR) : int(t * SR) + len(a)] = a
        spans.append((t + offsets[sid], t + offsets[sid] + len(a) / SR))
    return audio, spans


ticks = {"a": 0, "b": 0, "c": 0}


async def frames(sid: str, audio: np.ndarray):
    for f in iter_frames(audio, FRAME_SIZE):
        ticks[sid] += 1
        yield f
        await asyncio.sleep(0)


class SlowProvider(MockProvider):
    """Every send takes a while, so later turns must queue; flags any interleaving."""

    def __init__(self):
        super().__init__()
        self.busy = 0
        self.overlap = False

    async def send_turn(self, audio16k, speaker_label):
        self.busy += 1
        self.overlap |= self.busy > 1
        await asyncio.sleep(0.2)
        await super().send_turn(audio16k, speaker_label)
        self.busy -= 1


async def main() -> None:
    provider = SlowProvider()
    room = RoomPipeline(
        provider,
        lambda sid, claimed: IsolationPolicy(claimed, threshold, embedder="toy"),
        Matcher(sigs, threshold=threshold, embedder=emb),
        vad_factory=EnergyVAD,
        max_pending=1000,
        max_inflight_identify=1000,  # finite synthetic input: no busy drops under CPU load
        isolation="thread",
    )
    audio = {s: track(s) for s in "abc"}
    room.add_source("a", frames("a", audio["a"][0]), who["a"])
    room.add_source("b", frames("b", audio["b"][0]), who["b"])

    async def driver() -> None:
        while ticks["a"] * FRAME_SIZE / SR < JOIN_C:
            await asyncio.sleep(0)
        room.add_source("c", frames("c", audio["c"][0]), who["c"], t_offset=JOIN_C)
        while ticks["b"] * FRAME_SIZE / SR < CUT_B:
            await asyncio.sleep(0)
        room.remove_source("b")
        while ticks["a"] < len(audio["a"][0]) // FRAME_SIZE - 2 or "c" in room.sources:
            await asyncio.sleep(0.01)
        room.finish()

    await asyncio.gather(room.run(), driver())

    expected = []  # (end time, source), b's last turn ends where it was cut
    for sid in "abc":
        for a, b in audio[sid][1]:
            if sid == "b" and a > CUT_B - 3.0 and b > CUT_B:
                b = CUT_B
            expected.append((b, sid))
    expected.sort()
    recs = list(room.records)
    assert all(r.verdict is Verdict.ACCEPT and r.error is None for r in recs), recs
    got = [r.source_id for r in recs]
    assert got == [s for _, s in expected], (got, expected)
    assert [t.speaker_label for t in provider.turns] == [who[s] for s in got]
    assert len(provider.turns) == len(PLAN) == len(set((r.source_id, r.t_start) for r in recs))
    ends = [r.t_end for r in recs]
    assert ends == sorted(ends), ends
    assert not provider.overlap and room.max_concurrent_sends == 1
    assert all(r.queue_wait_s >= 0 for r in recs) and max(r.queue_wait_s for r in recs) > 0.05
    b_last = [r for r in recs if r.source_id == "b"][-1]
    assert b_last.t_end <= CUT_B + 1.0, b_last  # flushed on removal, not at its natural end
    c_first = [r for r in recs if r.source_id == "c"][0]
    assert c_first.t_start >= JOIN_C, c_first  # room time = join offset + local time
    print("room mux ok:", [(r.source_id, round(r.t_end, 1)) for r in recs])


asyncio.run(main())
