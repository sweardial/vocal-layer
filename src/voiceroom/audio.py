"""Audio helpers. Internal format: 16 kHz mono float32 in [-1, 1]."""

from collections.abc import Iterator
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

SAMPLE_RATE = 16000


def resample(x: np.ndarray, sr_in: int, sr_out: int = SAMPLE_RATE) -> np.ndarray:
    """Resample a 1-D float array with a polyphase filter; returns float32."""
    if sr_in <= 0 or sr_out <= 0:
        raise ValueError("sample rates must be positive")
    if sr_in == sr_out:
        return np.array(x, dtype=np.float32)
    g = gcd(sr_in, sr_out)
    return resample_poly(x, sr_out // g, sr_in // g).astype(np.float32)


class StreamResampler:
    """Click-free chunked resampling: same output as `resample` on the whole signal (up to
    float error), delayed by a few ms of lookahead. Call `flush()` after the last chunk."""

    def __init__(self, sr_in: int, sr_out: int = SAMPLE_RATE) -> None:
        if sr_in <= 0 or sr_out <= 0:
            raise ValueError("sample rates must be positive")
        g = gcd(sr_in, sr_out)
        self.up, self.down = sr_out // g, sr_in // g
        self.passthrough = sr_in == sr_out
        ctx = 12 * max(self.up, self.down) / self.up  # > filter half-length, in input samples
        self.ctx = self.down * int(np.ceil(ctx / self.down))
        self.reset()

    def reset(self) -> None:
        self._buf = np.zeros(self.ctx, dtype=np.float32)  # left context, then pending input
        self._in = self._out = 0  # samples pushed / emitted since reset

    def _process(self, final: bool) -> np.ndarray:
        n = len(self._buf)
        end = n if final else n - self.ctx  # emit input [ctx, end); right context is lookahead
        count = (end - self.ctx) // self.down * self.down
        if count <= 0:
            return np.zeros(0, dtype=np.float32)
        end = self.ctx + count
        window = self._buf[: end + (0 if final else self.ctx)]
        if final:
            window = np.concatenate([window, np.zeros(self.ctx, dtype=np.float32)])
        y = resample_poly(window, self.up, self.down).astype(np.float32)
        skip = self.ctx * self.up // self.down
        self._buf = self._buf[end - self.ctx :]
        out = y[skip : skip + count * self.up // self.down]
        self._out += len(out)
        return out

    def push(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        if self.passthrough:
            return x.copy()
        self._in += len(x)
        self._buf = np.concatenate([self._buf, x])
        return self._process(final=False)

    def flush(self) -> np.ndarray:
        if self.passthrough:
            return np.zeros(0, dtype=np.float32)
        pad = -(len(self._buf) - self.ctx) % self.down
        self._buf = np.concatenate([self._buf, np.zeros(pad, dtype=np.float32)])
        before = self._out
        out = self._process(final=True)
        want = -(-self._in * self.up // self.down) - before  # ceil, as resample_poly
        out = out[: max(want, 0)]
        self.reset()
        return out


def to_mono(x: np.ndarray) -> np.ndarray:
    """Average channels of a (samples,) or (samples, channels) array."""
    return x if x.ndim == 1 else x.mean(axis=1)


def load_audio(path: str | Path, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Load any wav/flac (anything libsndfile reads) as mono float32 at `sr`."""
    data, sr_in = sf.read(str(path), dtype="float32", always_2d=True)
    return resample(to_mono(data), sr_in, sr)


def float_to_pcm16(x: np.ndarray) -> np.ndarray:
    """Float [-1, 1] -> int16 (clipped, rounded)."""
    if not np.isfinite(x).all():
        raise ValueError("non-finite samples")
    return np.round(np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)


def pcm16_to_float(x: np.ndarray) -> np.ndarray:
    """int16 -> float32 in [-1, 1]."""
    return (np.asarray(x, dtype=np.float32) / 32768.0).astype(np.float32)


def iter_frames(x: np.ndarray, frame_size: int, pad: bool = True) -> Iterator[np.ndarray]:
    """Yield consecutive frames of `frame_size`; the last is zero-padded if `pad`, else dropped."""
    if frame_size <= 0:
        raise ValueError("frame_size must be positive")
    if x.ndim != 1:
        raise ValueError("iter_frames expects mono (1-D) audio")
    for start in range(0, len(x), frame_size):
        frame = x[start : start + frame_size]
        if len(frame) < frame_size:
            if not pad:
                return
            frame = np.pad(frame, (0, frame_size - len(frame)))
        yield frame
