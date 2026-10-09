"""Streaming voice activity detection: fixed-size frames in, speech probability out."""

from pathlib import Path
from typing import Protocol

import numpy as np

from .audio import SAMPLE_RATE

FRAME_SIZE = 512  # 32 ms at 16 kHz (D-003); Silero v5 requires exactly this at 16 kHz
FRAME_S = FRAME_SIZE / SAMPLE_RATE
DEFAULT_MODEL = Path(__file__).resolve().parents[2] / "models" / "silero_vad.onnx"

_CONTEXT = 64  # Silero v5 prepends the last 64 samples of the previous frame at 16 kHz


class VAD(Protocol):
    frame_size: int

    def process(self, frame: np.ndarray) -> float:
        """Speech probability in [0, 1] for one frame of `frame_size` float32 samples."""
        ...

    def reset(self) -> None: ...


def _check(frame: np.ndarray, frame_size: int) -> np.ndarray:
    frame = np.asarray(frame)
    if not np.issubdtype(frame.dtype, np.floating):
        raise TypeError("frames must be float in [-1, 1]; convert int16 with audio.pcm16_to_float")
    frame = frame.astype(np.float32, copy=False)
    if frame.shape != (frame_size,):
        raise ValueError(f"expected a frame of {frame_size} samples, got {frame.shape}")
    return frame


class SileroVAD:
    """Silero VAD v5 through onnxruntime; recurrent state and context carry across frames."""

    frame_size = FRAME_SIZE

    def __init__(self, model_path: str | Path = DEFAULT_MODEL):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT), dtype=np.float32)

    def process(self, frame: np.ndarray) -> float:
        x = np.concatenate([self._context, _check(frame, FRAME_SIZE)[None]], axis=1)
        out, self._state = self._sess.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -_CONTEXT:]
        return float(out[0, 0])


class EnergyVAD:
    """RMS-energy VAD for clean synthetic audio: probability ramps over [low_db, high_db]."""

    frame_size = FRAME_SIZE

    def __init__(self, low_db: float = -34.0, high_db: float = -26.0):
        if high_db <= low_db:
            raise ValueError("high_db must exceed low_db")
        self.low_db, self.high_db = low_db, high_db

    def reset(self) -> None:
        pass

    def process(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(_check(frame, FRAME_SIZE) ** 2)))
        db = 20 * np.log10(rms + 1e-10)
        return float(np.clip((db - self.low_db) / (self.high_db - self.low_db), 0.0, 1.0))
