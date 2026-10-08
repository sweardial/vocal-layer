"""Smoke: audio utilities and synthetic speakers."""

import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from voiceroom import audio, synth

SR = audio.SAMPLE_RATE

# Resample length/dtype, via file load at 44.1 kHz stereo.
x = synth.utterance(1, 1.0)
assert x.dtype == np.float32 and len(x) == SR
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "a.wav"
    up = audio.resample(x, SR, 44100)
    sf.write(p, np.stack([up, up], axis=1), 44100)
    y = audio.load_audio(p)
    f = Path(d) / "a.flac"
    sf.write(f, x, SR)
    assert audio.load_audio(f).shape == x.shape
assert y.dtype == np.float32 and y.ndim == 1 and abs(len(y) - SR) <= 1, (y.dtype, y.shape)
assert np.corrcoef(x[: len(y)], y[: len(x)])[0, 1] > 0.99

# PCM round trip.
assert np.max(np.abs(audio.pcm16_to_float(audio.float_to_pcm16(x)) - x)) < 1e-3
assert audio.float_to_pcm16(np.array([2.0, -2.0], np.float32)).tolist() == [32767, -32767]

# Frame coverage.
frames = list(audio.iter_frames(x[:1000], 512))
assert len(frames) == 2 and all(len(fr) == 512 for fr in frames)
assert np.array_equal(np.concatenate(frames)[:1000], x[:1000])
assert len(list(audio.iter_frames(x[:1000], 512, pad=False))) == 1

# Determinism and speaker distinctness.
assert np.array_equal(synth.utterance(1, 1.0), synth.utterance(1, 1.0))


def spec(s):
    m = np.abs(np.fft.rfft(s * np.hanning(len(s))))
    m = np.log(m + 1e-6)
    return (m - m.mean()) / m.std()


same = np.corrcoef(spec(synth.utterance(1, 1.0, seed=0)), spec(synth.utterance(1, 1.0, seed=1)))[
    0, 1
]
diffs = [np.corrcoef(spec(x), spec(synth.utterance(s, 1.0)))[0, 1] for s in (2, 3, 4, 5)]
assert max(diffs) < same, (same, diffs)

# Noise SNR within +/-1 dB (measured on the added component).
for kind in ("white", "pink", "brown"):
    for snr in (0, 10, 20):
        noisy = synth.add_noise(x, kind, snr, seed=3)
        n = noisy - x
        got = 10 * np.log10(np.mean(x.astype(np.float64) ** 2) / np.mean(n.astype(np.float64) ** 2))
        assert abs(got - snr) <= 1, (kind, snr, got)
# ref_power makes pauses noisy and fixes SNR relative to active speech.
gapped = np.concatenate([x, np.zeros_like(x)])
n2 = synth.add_noise(gapped, "white", 10, ref_power=float(np.mean(x**2))) - gapped
assert np.std(n2[len(x) :]) > 0
print("audio ok")
