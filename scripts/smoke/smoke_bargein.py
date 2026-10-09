"""Smoke: barge-in. An owner turn mid-reply interrupts once; a non-owner turn does not."""

import asyncio

import numpy as np

from voiceroom.adapters import MockProvider
from voiceroom.audio import iter_frames
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import make_conversation, split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.io import SpeakerSink
from voiceroom.matcher import Matcher
from voiceroom.pipeline import Pipeline
from voiceroom.policy import IsolationPolicy, Verdict
from voiceroom.segmenter import Segmenter
from voiceroom.vad import FRAME_SIZE, EnergyVAD

corpus = SynthCorpus(3, seed=1, utts_per_speaker=8)
spks = corpus.speakers()
owner = spks[0]
emb = get_embedder("toy")
enroll_utts, held = split_utterances(corpus, spks, n_enroll=4)
sigs = [enroll(s, np.concatenate([u.load() for u in enroll_utts[s]]), emb) for s in spks]
cal = Matcher(sigs, threshold=0.0)
gen, imp = [], []
for s in spks:
    for u in held[s]:
        sc = cal.identify_embedding(emb.embed(u.load()[:16000])).scores
        gen.append(sc[s])
        imp += [v for k, v in sc.items() if k != s]
threshold = (float(np.percentile(gen, 5)) + float(np.percentile(imp, 95))) / 2
conv = make_conversation(
    corpus, spks, n_turns=6, pause_range=(0.6, 1.2), snr_db=None, seed=11, n_enroll=4
)


def seg_audio(owner_side: bool) -> np.ndarray:
    a, b, _ = next(g for g in conv.segments if (g[2] == owner) == owner_side)
    return conv.audio[int(a * 16000) : int(b * 16000)]


async def run(second_is_owner: bool) -> tuple[MockProvider, SpeakerSink, Pipeline]:
    provider = MockProvider(reply_s=30.0)
    sink = SpeakerSink(sample_rate=16000)  # headless: no device, the callback is driven by hand
    sink._stream = object()
    pipe = Pipeline(
        EnergyVAD(),
        Segmenter(),
        Matcher(sigs, threshold=threshold, embedder=emb),
        IsolationPolicy(
            owner, threshold, early_score_buffer=0.02, early_margin=0.02, embedder="toy"
        ),
        provider,
        max_pending=1000,
        isolation="thread",
        sink=sink,
    )
    gap = np.zeros(16000, np.float32)
    first = np.concatenate([seg_audio(True), gap])
    second = np.concatenate([seg_audio(second_is_owner), gap])

    async def frames():
        for f in iter_frames(first, FRAME_SIZE):
            yield f
        await asyncio.sleep(0.2)  # let the first reply reach the sink
        sink._callback(np.zeros((1600, 1), np.float32), 1600, None, None)  # 100 ms "played"
        for f in iter_frames(second, FRAME_SIZE):
            yield f

    play = asyncio.create_task(_play(provider, sink))
    await pipe.run(frames())
    await provider.close()
    await play
    return provider, sink, pipe


async def _play(provider, sink) -> None:
    while not provider.connected:
        await asyncio.sleep(0.01)
    async for c in provider.audio_out():
        sink.write(c)


provider, sink, pipe = asyncio.run(run(True))
assert [r.verdict for r in pipe.records] == [Verdict.ACCEPT] * 2, pipe.records
assert provider.interrupts == [100], provider.interrupts

provider, sink, pipe = asyncio.run(run(False))
assert [r.verdict for r in pipe.records] == [Verdict.ACCEPT, Verdict.REJECT], pipe.records
assert provider.interrupts == [] and sink.active
print("bargein ok")
