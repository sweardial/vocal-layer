"""Smoke: conversations with 3 enrolled speakers + 1 unknown through the collaboration pipeline."""

import asyncio

import numpy as np

from voiceroom.adapters import MockProvider
from voiceroom.audio import iter_frames
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import make_conversation, split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.matcher import Matcher
from voiceroom.pipeline import Pipeline
from voiceroom.policy import UNKNOWN_LABEL, CollaborationPolicy, Verdict
from voiceroom.segmenter import Segmenter
from voiceroom.vad import FRAME_SIZE, EnergyVAD

corpus = SynthCorpus(4, seed=2, utts_per_speaker=12)
spks = corpus.speakers()
enrolled_names, stranger = spks[:3], spks[3]
emb = get_embedder("toy")
enroll_utts, held = split_utterances(corpus, spks, n_enroll=6)
sigs = [enroll(s, np.concatenate([u.load() for u in enroll_utts[s]]), emb) for s in enrolled_names]
cal = Matcher(sigs, threshold=0.0)
gen, imp = [], []
for s in spks:
    for u in held[s]:
        sc = cal.identify_embedding(emb.embed(u.load()[:16000])).scores
        gen += [sc[s]] if s in enrolled_names else []
        imp += [v for k, v in sc.items() if k != s or s == stranger]
threshold = (float(np.percentile(gen, 5)) + float(np.percentile(imp, 95))) / 2
print(f"threshold={threshold:.3f}")


def run(unknown: str, seed: int):
    conv = make_conversation(
        corpus, spks, n_turns=8, pause_range=(0.6, 1.2), snr_db=None, seed=seed, n_enroll=6
    )
    provider = MockProvider()
    pipe = Pipeline(
        EnergyVAD(),
        Segmenter(),
        Matcher(sigs, threshold=threshold, embedder=emb),
        CollaborationPolicy(
            enrolled_names,
            unknown,
            early_score_buffer=0.02,
            early_margin=0.02,
        ),
        provider,
        max_pending=1000,
        isolation="thread",
    )
    asyncio.run(pipe.run(iter_frames(conv.audio, FRAME_SIZE)))
    return conv, pipe, provider


for unknown in ("drop", "label"):
    total = correct = n_unknown = 0
    for seed in (21, 22):
        conv, pipe, provider = run(unknown, seed)
        committed = [r for r in pipe.records if r.verdict is Verdict.ACCEPT]
        assert len(committed) == len(provider.turns)
        by_turn = dict(zip(map(id, committed), provider.turns, strict=True))
        for r in pipe.records:
            seg = max(conv.segments, key=lambda s: min(s[1], r.t_end) - max(s[0], r.t_start))
            truth = seg[2] if seg[2] != stranger else None
            if truth is None:
                n_unknown += 1
            if r.verdict is Verdict.ACCEPT:
                label = by_turn[id(r)].speaker_label
                total += 1
                correct += label == (truth or UNKNOWN_LABEL)
                if unknown == "drop":
                    assert truth is not None, "unknown speaker committed under drop"
            elif truth is not None or unknown == "label":
                total += 1  # a turn that should have been forwarded but was dropped is wrong
        if unknown == "drop":
            assert all(t.speaker_label != UNKNOWN_LABEL for t in provider.turns)
    assert n_unknown > 0
    print(f"unknown={unknown}: label accuracy {correct}/{total}, unknown turns={n_unknown}")
    assert total and correct >= 0.95 * total, (correct, total)
    if unknown == "label":
        assert any(t.speaker_label == UNKNOWN_LABEL for t in provider.turns)
print("e2e collab ok")
