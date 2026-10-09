"""Deterministic synthetic speakers and noise, so smoke checks run offline."""

from dataclasses import dataclass

import numpy as np
from scipy.signal import lfilter

from .audio import SAMPLE_RATE


@dataclass(frozen=True)
class SpeakerProfile:
    f0: float  # mean pitch, Hz
    formants: tuple[float, float, float]  # Hz
    jitter: float  # relative f0 perturbation
    vibrato_hz: float
    vibrato_depth: float  # relative


def make_speaker(seed: int) -> SpeakerProfile:
    """Seeded speaker: same seed -> same profile."""
    r = np.random.default_rng(seed)
    return SpeakerProfile(
        f0=float(r.uniform(85, 255)),
        formants=(
            float(r.uniform(300, 900)),
            float(r.uniform(900, 2300)),
            float(r.uniform(2300, 3300)),
        ),
        jitter=float(r.uniform(0.002, 0.02)),
        vibrato_hz=float(r.uniform(3, 7)),
        vibrato_depth=float(r.uniform(0.005, 0.03)),
    )


def _resonator(freq: float, bw: float, sr: int) -> tuple[np.ndarray, np.ndarray]:
    r = np.exp(-np.pi * bw / sr)
    theta = 2 * np.pi * freq / sr
    return np.array([1.0 - r]), np.array([1.0, -2 * r * np.cos(theta), r * r])


def utterance(
    speaker: SpeakerProfile | int, duration: float, seed: int = 0, sr: int = SAMPLE_RATE
) -> np.ndarray:
    """Voiced source (jittered, vibrato f0) through formant resonators; peak-normalized to 0.5."""
    if isinstance(speaker, int):
        speaker = make_speaker(speaker)
    n = int(round(duration * sr))
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    # Slowly varying jitter (smoothed noise) on top of vibrato.
    jit = lfilter([0.05], [1, -0.95], rng.standard_normal(n)) * speaker.jitter * 4
    f0 = speaker.f0 * (1 + speaker.vibrato_depth * np.sin(2 * np.pi * speaker.vibrato_hz * t) + jit)
    phase = 2 * np.pi * np.cumsum(f0) / sr
    # Glottal-ish source: harmonics with 1/k rolloff below Nyquist.
    src = np.zeros(n)
    for k in range(1, int(min(sr / 2, 5000) // speaker.f0) + 1):
        src += np.sin(k * phase) / k
    src += 0.02 * rng.standard_normal(n)
    out = src
    for f, bw in zip(speaker.formants, (80.0, 120.0, 160.0), strict=True):
        b, a = _resonator(f, bw, sr)
        out = lfilter(b, a, out)
    # Syllable-rate amplitude envelope, with short fades.
    out *= 0.6 + 0.4 * np.sin(2 * np.pi * 3.5 * t) ** 2
    fade = min(n // 2, int(0.01 * sr))
    if fade:
        ramp = np.linspace(0, 1, fade)
        out[:fade] *= ramp
        out[n - fade :] *= ramp[::-1]
    peak = np.max(np.abs(out)) if n else 0.0
    return (out * (0.5 / peak) if peak > 0 else out).astype(np.float32)


def noise(kind: str, n: int, seed: int = 0) -> np.ndarray:
    """Unit-RMS white / pink / brown noise."""
    rng = np.random.default_rng(seed)
    w = rng.standard_normal(n)
    if kind == "white":
        x = w
    elif kind == "pink":  # Paul Kellet's economy filter
        x = lfilter(
            [0.049922035, -0.095993537, 0.050612699, -0.004408786],
            [1, -2.494956002, 2.017265875, -0.522189400],
            w,
        )
    elif kind == "brown":
        x = lfilter([1.0], [1, -0.995], w)  # leaky integrator keeps it bounded
    else:
        raise ValueError(f"unknown noise kind: {kind!r}")
    x = x - x.mean()
    rms = np.sqrt(np.mean(x**2))
    return (x / rms if rms > 0 else x).astype(np.float32)


def add_noise(
    x: np.ndarray, kind: str, snr_db: float, seed: int = 0, ref_power: float | None = None
) -> np.ndarray:
    """Mix noise into `x` at the target SNR (dB, by mean power).

    Signal power defaults to the mean over all of `x`. For input with silent pauses pass
    `ref_power` (e.g. the mean power of the active speech) so the SNR during speech is as
    requested and pauses are noisy too.
    """
    if len(x) < 2:
        return np.asarray(x, dtype=np.float32).copy()
    p_sig = float(np.mean(np.square(x, dtype=np.float64))) if ref_power is None else ref_power
    scale = np.sqrt(p_sig / 10 ** (snr_db / 10))
    return (x + scale * noise(kind, len(x), seed)).astype(np.float32)


def add_bleed(
    track: np.ndarray,
    source: np.ndarray,
    level_db: float = -12.0,
    delay_ms: float = 15.0,
    noise_snr_db: float | None = 20.0,
    seed: int = 0,
) -> np.ndarray:
    """Mix a copy of `source` (another mic's signal, same length scale) into `track`: attenuated
    by `level_db` (-10..-20 typical), delayed by `delay_ms` (5-40 typical), with pink noise at
    `noise_snr_db` relative to the copy (None = no noise), present only while the copy is."""
    n = len(track)
    d = int(round(delay_ms * 1e-3 * SAMPLE_RATE))
    copy = np.zeros(n, dtype=np.float64)
    m = max(0, min(len(source), n - d))
    copy[d : d + m] = source[:m]
    copy *= 10 ** (level_db / 20)
    if noise_snr_db is not None and copy.any():
        # noise rides on the copy (only where the other mic is speaking): the pauses stay quiet
        p = float(np.mean(np.square(copy[copy != 0])))
        hop = 512
        act = np.repeat([np.any(copy[i : i + hop] != 0) for i in range(0, n, hop)], hop)[:n]
        noisy = add_noise(copy, "pink", noise_snr_db, seed=seed, ref_power=p)
        copy = np.where(act, noisy, copy)
    return (track + copy).astype(np.float32)
