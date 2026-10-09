"""Smoke: EnergyVAD on a synthetic conversation; SileroVAD on LibriSpeech (SKIPs if absent)."""

import sys
import time
from pathlib import Path

import numpy as np

from voiceroom.audio import SAMPLE_RATE, iter_frames
from voiceroom.eval.conversations import make_conversation
from voiceroom.eval.corpus import LibriSpeechCorpus, SynthCorpus
from voiceroom.vad import DEFAULT_MODEL, FRAME_S, FRAME_SIZE, EnergyVAD, SileroVAD

LIBRI = Path(__file__).resolve().parents[2] / "data" / "LibriSpeech"


# Ground truth is per utterance, but read speech has real pauses and padded edges. A VAD-off run
# inside a segment up to this long is a legitimate pause, not an error; turn gaps are strict.
MAX_PAUSE_S = 0.5


def forgive_pauses(pred: np.ndarray, truth: np.ndarray, max_pause_s: float) -> np.ndarray:
    """Per-frame correctness; short VAD-off runs inside ground-truth speech count as correct."""
    ok = pred == truth
    n, i = len(pred), 0
    while i < n:
        if truth[i] and not pred[i]:
            j = i
            while j < n and truth[j] and not pred[j]:
                j += 1
            if (j - i) * FRAME_S <= max_pause_s:
                ok[i:j] = True
            i = j
        else:
            i += 1
    return ok


def evaluate(vad, conv) -> dict[str, float]:
    """Frame stats vs ground truth (frame centre inside a segment), plus real-time factor."""
    vad.reset()
    t = time.perf_counter()
    probs = np.array([vad.process(f) for f in iter_frames(conv.audio, FRAME_SIZE)])
    rtf = (time.perf_counter() - t) / (len(conv.audio) / SAMPLE_RATE)
    centres = (np.arange(len(probs)) + 0.5) * FRAME_S
    truth = np.zeros(len(probs), bool)
    for a, b, _ in conv.segments:
        truth |= (centres >= a) & (centres < b)
    pred = probs >= 0.5
    return {
        "agree": float(np.mean(forgive_pauses(pred, truth, MAX_PAUSE_S))),
        "recall": float(np.mean(pred[truth])),  # strict: pauses inside utterances count as misses
        "gap_fp": float(np.mean(pred[~truth])),
        "rtf": rtf,
    }


synth = SynthCorpus(3, seed=1, utts_per_speaker=6)
conv = make_conversation(
    synth, synth.speakers(), n_turns=8, pause_range=(0.3, 0.8), snr_db=20, seed=3
)
m = evaluate(EnergyVAD(), conv)
print("energy", {k: round(v, 3) for k, v in m.items()})
assert m["agree"] >= 0.90 and m["gap_fp"] <= 0.05 and m["recall"] >= 0.90, m

if not DEFAULT_MODEL.exists() or not LIBRI.is_dir():
    print("SKIP silero: need models/silero_vad.onnx and data/LibriSpeech")
    sys.exit(0)

libri = LibriSpeechCorpus(LIBRI)
conv = make_conversation(libri, libri.speakers()[:2], n_turns=6, pause_range=(0.5, 1.0), seed=1)
vad = SileroVAD()
m = evaluate(vad, conv)
print("silero", {k: round(v, 3) for k, v in m.items()})
# Recall is strict and read speech has real pauses, so ~0.75 is expected; a VAD that drops its
# recurrent state scores ~0.5, and an always-on VAD fails the gap check.
assert m["agree"] >= 0.90 and m["gap_fp"] <= 0.05 and m["recall"] >= 0.65, m
assert m["rtf"] < 0.1, m

# State is carried: the same frame scores differently after warm-up than after reset().
audio = conv.audio[: FRAME_SIZE * 40]
frames = list(iter_frames(audio, FRAME_SIZE))
vad.reset()
first = [vad.process(f) for f in frames]
vad.reset()
assert first == [vad.process(f) for f in frames], "non-deterministic after reset"
warm = vad.process(frames[10])
vad.reset()
assert warm != vad.process(frames[10]), "state not carried"
print("vad ok")
