"""Synthetic noise for enrollment augmentation (not the white noise the bench tests with)."""

import numpy as np


def _colored(n: int, rng: np.random.Generator, kind: str) -> np.ndarray:
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.maximum(np.arange(len(spec)), 1).astype(np.float64)
    spec *= f ** (-0.5 if kind == "pink" else -1.0)  # pink: 1/f power; brown: 1/f^2
    x = np.fft.irfft(spec, n)
    return x / (np.std(x) + 1e-12)


def add_noise(
    audio: np.ndarray, snr_db: float, rng: np.random.Generator, kind: str = "pink"
) -> np.ndarray:
    """Add pink/brown noise at `snr_db` relative to the clip's mean power."""
    audio = np.asarray(audio, np.float32)
    power = float(np.mean(audio**2)) + 1e-12
    noise = _colored(len(audio), rng, kind) * np.sqrt(power / 10 ** (snr_db / 10))
    return (audio + noise).astype(np.float32)
