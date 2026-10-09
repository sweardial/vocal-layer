"""Smoke: synthetic conversation -> EnergyVAD -> Segmenter; turn boundaries vs ground truth."""

import numpy as np

from voiceroom.audio import iter_frames
from voiceroom.eval.conversations import make_conversation
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.segmenter import IdentifyReady, Segmenter, SpeechStart, TurnEnd
from voiceroom.vad import FRAME_SIZE, EnergyVAD

TOL_S = 0.3

synth = SynthCorpus(3, seed=1, utts_per_speaker=6)
conv = make_conversation(
    synth, synth.speakers(), n_turns=10, pause_range=(0.6, 1.2), snr_db=20, seed=5
)


def run(seg: Segmenter, audio: np.ndarray) -> list:
    vad = EnergyVAD()
    events = []
    for f in iter_frames(audio, FRAME_SIZE):
        events += seg.process(f, vad.process(f))
    return events + seg.flush()


events = run(Segmenter(), conv.audio)
turns = [e for e in events if isinstance(e, TurnEnd)]
starts = [e for e in events if isinstance(e, SpeechStart)]
ready = [e for e in events if isinstance(e, IdentifyReady)]
assert len(starts) == len(turns), (len(starts), len(turns))

good = 0
for a, b, _ in conv.segments:
    hits = [t for t in turns if t.t_start < b and t.t_end > a]
    if len(hits) == 1 and abs(hits[0].t_start - a) <= TOL_S and abs(hits[0].t_end - b) <= TOL_S:
        good += 1
frac = good / len(conv.segments)
print(f"{len(conv.segments)} segments, {len(turns)} turns, {good} within {TOL_S}s ({frac:.0%})")
assert frac >= 0.9, frac

for t in turns:  # no turn spans two ground-truth speakers (or two segments)
    overlapping = [s for s in conv.segments if t.t_start < s[1] and t.t_end > s[0]]
    assert len({s[2] for s in overlapping}) <= 1 and len(overlapping) <= 1, (t.t_start, overlapping)
    assert len(t.audio) > 0 and not t.split

# IdentifyReady fires only for turns with >= 1 s of audio, before the turn ends, once per turn.
for r in ready:
    assert len(r.audio) / 16000 >= 1.0 - 1e-6
    assert any(t.t_start == r.t_start for t in turns)
assert len(ready) == len({r.t_start for r in ready})
assert len(ready) >= 0.7 * len(turns), (len(ready), len(turns))

# Max-turn split: a long run of speech is cut, with continuity (no lost audio).
speech = np.concatenate([conv.audio[int(a * 16000) : int(b * 16000)] for a, b, _ in conv.segments])
long_events = run(Segmenter(max_turn_s=4.0), np.concatenate([speech, np.zeros(16000, np.float32)]))
cut = [e for e in long_events if isinstance(e, TurnEnd)]
assert any(t.split for t in cut) and not cut[-1].split, [t.split for t in cut]
assert all(t.t_end - t.t_start <= 4.0 + 0.1 for t in cut)
# Split edge cases: no silent turns, no crash on flush/tail=0/inf, identify counts speech only.
for kw in ({}, {"tail_s": 0.0}, {"max_turn_s": float("inf")}):
    for n in range(100, 140):
        seg = Segmenter(**{"max_turn_s": 2.0, **kw})
        ev = run(seg, speech[: n * FRAME_SIZE])
        assert all(
            t.t_end > t.t_start and np.abs(t.audio).max() > 0 for t in ev if isinstance(t, TurnEnd)
        )
gappy = np.tile(np.r_[speech[: 4 * FRAME_SIZE], np.zeros(8 * FRAME_SIZE, np.float32)], 6)
assert not [e for e in run(Segmenter(end_silence_s=0.6), gappy) if isinstance(e, IdentifyReady)]
print("segmenter ok")
