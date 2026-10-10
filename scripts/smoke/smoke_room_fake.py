"""Smoke: FakeRoom with 2 participants joining at different times, one leaving mid-turn, driven
through RoomAgent into MockProvider: labels come from identity, reply audio is published."""

import asyncio

import numpy as np

from voiceroom.adapters import MockProvider
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.matcher import Matcher
from voiceroom.policy import IsolationPolicy
from voiceroom.room import RoomPipeline
from voiceroom.rooms import FakeParticipant, FakeRoom, RoomAgent
from voiceroom.vad import EnergyVAD

SR = 16000
corpus = SynthCorpus(2, seed=1, utts_per_speaker=12)
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


def track(spk: str, idxs: list[int], gap: float = 1.0) -> np.ndarray:
    parts = []
    for i in idxs:
        parts += [np.zeros(int(gap * SR), np.float32), held[spk][i].load()[: int(2.5 * SR)]]
    return np.concatenate(parts + [np.zeros(SR, np.float32)]).astype(np.float32)


LEAVE_B = 6.0  # b joins at 4.0 and leaves 2 s in, during its first utterance
a_audio = track(spks[0], [0, 1])
b_audio = track(spks[1], [0, 1])
room = FakeRoom(
    [
        FakeParticipant("pa", a_audio, identity=spks[0], join_at=0.0),
        FakeParticipant("pb", b_audio, identity=spks[1], join_at=4.0, leave_at=LEAVE_B),
    ]
)
provider = MockProvider()
pipeline = RoomPipeline(
    provider,
    lambda sid, claimed: IsolationPolicy(claimed, threshold, embedder="toy"),
    Matcher(sigs, threshold=threshold, embedder=emb),
    vad_factory=EnergyVAD,
    max_pending=1000,
    max_inflight_identify=1000,
    isolation="thread",
)
agent = RoomAgent(room, pipeline, provider)
asyncio.run(asyncio.wait_for(agent.run(), 60))

recs = list(pipeline.records)
by = {s: [r for r in recs if r.source_id == s] for s in ("pa", "pb")}
assert by["pa"] and by["pb"], recs
assert "pb" not in pipeline.sources
assert all(r.t_start >= 4.0 for r in by["pb"]), by["pb"]  # b joined late (room time offset)
assert max(r.t_end for r in by["pb"]) <= LEAVE_B + 1.0, by["pb"]  # flushed on leaving
labels = {s: {t.speaker_label for t in provider.turns} for s in ("x",)}["x"]
assert labels == set(spks), labels
sent = [r for r in recs if r.verdict.name == "ACCEPT"]
assert len(sent) == len(provider.turns) >= 3, (len(sent), len(provider.turns))
for r, t in zip(sent, provider.turns, strict=True):
    assert t.speaker_label == (spks[0] if r.source_id == "pa" else spks[1]), (r, t.speaker_label)
assert len(room.published) == 3 * len(provider.turns), len(room.published)
assert all(len(c.samples) > 0 for c in room.published) and not room.connected
print("room fake ok:", [(r.source_id, round(r.t_end, 1)) for r in recs], len(room.published))
