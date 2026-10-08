"""Numpy-only toy embedder: log-mel mean/std statistics. For offline checks, not for real ID."""

import numpy as np

from voiceroom.audio import SAMPLE_RATE
from voiceroom.embedders.base import l2_normalize

N_FFT = 512
HOP = 160
N_MELS = 40


def _mel(f: np.ndarray) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + f / 700.0)


def _filterbank() -> np.ndarray:
    freqs = np.linspace(0, SAMPLE_RATE / 2, N_FFT // 2 + 1)
    pts = np.linspace(_mel(np.array(0.0)), _mel(np.array(SAMPLE_RATE / 2)), N_MELS + 2)
    hz = 700.0 * (10 ** (pts / 2595.0) - 1.0)
    fb = np.zeros((N_MELS, len(freqs)))
    for i in range(N_MELS):
        lo, c, hi = hz[i : i + 3]
        fb[i] = np.clip(np.minimum((freqs - lo) / (c - lo), (hi - freqs) / (hi - c)), 0, None)
    return fb


_FB = _filterbank()
_WIN = np.hanning(N_FFT)


_BINS = np.arange(N_MELS)


def _detrend(v: np.ndarray) -> np.ndarray:
    return v - np.polyval(np.polyfit(_BINS, v, 1), _BINS)


class ToyEmbedder:
    name = "toy"
    dim = 2 * N_MELS

    def embed(self, audio16k: np.ndarray) -> np.ndarray:
        x = np.asarray(audio16k, np.float64)
        if len(x) < N_FFT:
            x = np.pad(x, (0, N_FFT - len(x)))
        idx = np.arange(0, len(x) - N_FFT + 1, HOP)[:, None] + np.arange(N_FFT)
        spec = np.abs(np.fft.rfft(x[idx] * _WIN, axis=1)) ** 2
        logmel = np.log(spec @ _FB.T + 1e-8)
        # Drop near-silent frames so pauses don't dominate the statistics.
        energy = logmel.mean(axis=1)
        keep = energy > energy.max() - 8.0
        feats = logmel[keep]
        # Remove per-frame level (gain invariance).
        feats = feats - feats.mean(axis=1, keepdims=True)
        # Remove the shared tilt/offset (linear fit over mel bins) so cosine reflects
        # speaker-specific spectral shape rather than the common speech envelope.
        v = np.concatenate([_detrend(feats.mean(axis=0)), _detrend(feats.std(axis=0))])
        if not np.isfinite(v).all() or not np.any(v):
            # Degenerate input (silence, NaN): fixed unit vector, so the output stays unit-norm.
            v = np.ones(self.dim)
        return l2_normalize(v)
