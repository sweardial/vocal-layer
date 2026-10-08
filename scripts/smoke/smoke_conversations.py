"""Smoke: synthetic conversations on SynthCorpus (order, audio, pauses, determinism, split)."""

import tempfile
from pathlib import Path

import numpy as np

from voiceroom.audio import SAMPLE_RATE
from voiceroom.eval.conversations import (
    load_conversation,
    make_conversation,
    save_conversation,
    split_utterances,
)
from voiceroom.eval.corpus import SynthCorpus

corpus = SynthCorpus(4, seed=1, utts_per_speaker=6)
spks = corpus.speakers()
pr = (0.3, 0.8)
conv = make_conversation(corpus, spks, n_turns=8, pause_range=pr, snr_db=None, seed=5)

segs = conv.segments
assert len(segs) == 8 and all(s[0] < s[1] for s in segs)
for a, b in zip(segs, segs[1:], strict=False):
    gap = b[0] - a[1]
    assert gap >= 0 and a[0] < b[0], "overlap or unsorted"
    assert pr[0] - 1e-3 <= gap <= pr[1] + 1e-3, gap
    assert a[2] != b[2]

by_id = {u.id: u for s in spks for u in corpus.utterances(s)}
for (st, en, spk), uid in zip(segs, conv.utterance_ids, strict=True):
    u = by_id[uid]
    assert u.speaker == spk
    seg = conv.audio[round(st * SAMPLE_RATE) : round(en * SAMPLE_RATE)]
    assert np.array_equal(seg, u.load()), uid
assert len(conv.audio) == round(segs[-1][1] * SAMPLE_RATE)

# determinism, and that a different seed differs
again = make_conversation(corpus, spks, n_turns=8, pause_range=pr, snr_db=None, seed=5)
assert np.array_equal(conv.audio, again.audio) and conv.segments == again.segments
other = make_conversation(corpus, spks, n_turns=8, pause_range=pr, snr_db=None, seed=6)
assert other.segments != conv.segments

# split is disjoint and conversations never use enrollment audio
enroll, test = split_utterances(corpus, spks)
for s in spks:
    e, t = {u.id for u in enroll[s]}, {u.id for u in test[s]}
    assert e and t and not (e & t) and e | t == {u.id for u in corpus.utterances(s)}
enrolled = {i for ids in conv.enrollment_ids.values() for i in ids}
assert not enrolled & set(conv.utterance_ids)

# noise hits the requested SNR; segments unchanged
noisy = make_conversation(corpus, spks, n_turns=8, pause_range=pr, snr_db=10, seed=5)
assert noisy.segments == conv.segments
noise = noisy.audio - conv.audio
speech = np.concatenate(
    [conv.audio[round(a * SAMPLE_RATE) : round(b * SAMPLE_RATE)] for a, b, _ in segs]
)
snr = 10 * np.log10(np.mean(speech**2) / np.mean(noise**2))
assert abs(snr - 10) < 0.5, snr

# save/load roundtrip
with tempfile.TemporaryDirectory() as d:
    save_conversation(noisy, Path(d) / "c1")
    back = load_conversation(Path(d) / "c1")
assert np.array_equal(back.audio, noisy.audio) and back.segments == noisy.segments
assert back.utterance_ids == noisy.utterance_ids and back.enrollment_ids == noisy.enrollment_ids
print("conversations ok")
