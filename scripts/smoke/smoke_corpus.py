"""Smoke: SynthCorpus always; LibriSpeechCorpus only when data/LibriSpeech/dev-clean is present."""

import sys
from pathlib import Path

import numpy as np

from voiceroom.audio import SAMPLE_RATE
from voiceroom.eval.corpus import LibriSpeechCorpus, SynthCorpus

c = SynthCorpus(5, seed=3)
assert len(c.speakers()) == 5
for s in c.speakers():
    us = c.utterances(s)
    assert len(us) >= 2 and all(u.speaker == s and u.duration > 0 for u in us)
u = c.utterances(c.speakers()[0])[0]
x = u.load()
assert x.dtype == np.float32 and abs(len(x) - u.duration * SAMPLE_RATE) <= 1
assert np.array_equal(x, SynthCorpus(5, seed=3).utterances(u.speaker)[0].load())
print("synth ok")

root = Path(__file__).resolve().parents[2] / "data" / "LibriSpeech"
if not (root / "dev-clean").is_dir():
    print("SKIP LibriSpeech part: data/LibriSpeech/dev-clean not present")
    sys.exit(0)  # synth part passed; only the LibriSpeech part is skipped
lc = LibriSpeechCorpus(root, "dev-clean")
assert len(lc.speakers()) >= 40, len(lc.speakers())
u = lc.utterances(lc.speakers()[0])[0]
x = u.load()
assert x.dtype == np.float32 and abs(len(x) - u.duration * SAMPLE_RATE) <= 2
print(f"librispeech ok: {len(lc.speakers())} speakers")
