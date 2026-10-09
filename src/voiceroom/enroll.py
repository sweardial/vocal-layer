"""Enrollment: ~20-30 s of audio -> Signature (centroid + chunk embeddings)."""

from dataclasses import dataclass, field

import numpy as np

from voiceroom.audio import SAMPLE_RATE
from voiceroom.config import HOP_S, MIN_SPEECH_S, WINDOW_S
from voiceroom.embedders import Embedder, embed_batch
from voiceroom.embedders.base import l2_normalize

_FRAME = SAMPLE_RATE // 50  # 20 ms


class EnrollmentError(ValueError):
    pass


@dataclass(frozen=True, eq=False)  # identity eq/hash: array fields aren't comparable
class Signature:
    name: str
    embedder: str
    centroid: np.ndarray = field(repr=False)
    chunks: np.ndarray = field(repr=False)  # (n, dim)
    speech_seconds: float = 0.0

    def __repr__(self) -> str:
        return (
            f"Signature(name={self.name!r}, embedder={self.embedder!r}, "
            f"chunks={len(self.chunks)}, speech_seconds={self.speech_seconds:.1f})"
        )


def speech_mask(audio: np.ndarray) -> np.ndarray:
    """Per-20 ms-frame energy gate (placeholder until the real VAD lands). Bool array."""
    n = len(audio) // _FRAME
    if n == 0:
        return np.zeros(0, bool)
    rms = np.sqrt((audio[: n * _FRAME].reshape(n, _FRAME) ** 2).mean(axis=1))
    ref = np.percentile(rms, 95)
    # Spectral flatness rejects steady broadband noise (speech frames are far less flat).
    fr = audio[: n * _FRAME].reshape(n, _FRAME) * np.hanning(_FRAME)
    p = np.abs(np.fft.rfft(fr, axis=1)) ** 2 + 1e-12
    flat = np.exp(np.log(p).mean(axis=1)) / p.mean(axis=1)
    return (rms > 0.1 * ref) & (rms > 1e-3) & (flat < 0.1)


def enroll(name: str, audio: np.ndarray, embedder: Embedder) -> Signature:
    audio = np.asarray(audio, np.float32)
    if audio.ndim != 1:
        raise EnrollmentError("audio must be 1-D mono 16 kHz float32")
    mask = speech_mask(audio)
    speech_s = float(mask.sum()) * _FRAME / SAMPLE_RATE
    if speech_s < MIN_SPEECH_S:
        raise EnrollmentError(f"need >= {MIN_SPEECH_S:.0f} s of speech, got {speech_s:.1f} s")
    win, hop = int(WINDOW_S * SAMPLE_RATE), int(HOP_S * SAMPLE_RATE)
    clips = []
    for s in range(0, max(len(audio) - win, 0) + 1, hop):
        w = audio[s : s + win]
        if speech_mask(w).mean() >= 0.5:
            clips.append(w)
    if not clips:
        raise EnrollmentError("no usable speech windows")
    chunks = embed_batch(embedder, clips).astype(np.float32)
    centroid = l2_normalize(chunks.mean(axis=0))
    return Signature(name, embedder.name, centroid, chunks, speech_s)
