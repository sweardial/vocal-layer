"""WeSpeaker ResNet34-LM (VoxCeleb) on onnxruntime with a Kaldi-compatible fbank front end.

Front end matches wespeaker's `compute_fbank`: waveform scaled to int16 range, 80 mel bins,
25 ms hamming window / 10 ms shift, dither 0 at inference, then per-utterance mean normalisation.
Model: CC-BY-4.0, see models/README (written by scripts/fetch_models.py).
"""

import os
from pathlib import Path

import kaldi_native_fbank as knf
import numpy as np
import onnxruntime as ort

from voiceroom.audio import SAMPLE_RATE
from voiceroom.embedders.base import l2_normalize

DEFAULT_MODEL = Path(__file__).resolve().parents[3] / "models" / "voxceleb_resnet34_LM.onnx"
N_MELS = 80
# Inference cost is linear in clip length (~55 ms/s single-threaded) and scales ~linearly with
# threads up to 4, which is what removed the long-clip p95 tail.
N_THREADS = max(1, min(4, os.cpu_count() or 1))
MIN_SAMPLES = SAMPLE_RATE  # shorter inputs give NaN (std pooling over ~1 frame); pad to 1 s


def _fbank(audio16k: np.ndarray) -> np.ndarray:
    opts = knf.FbankOptions()
    opts.frame_opts.samp_freq = SAMPLE_RATE
    opts.frame_opts.frame_length_ms = 25.0
    opts.frame_opts.frame_shift_ms = 10.0
    opts.frame_opts.dither = 0.0
    opts.frame_opts.snip_edges = True
    opts.frame_opts.window_type = (
        "hamming"  # wespeaker passes window_type='hamming' (knf default: povey)
    )
    opts.use_energy = False
    opts.mel_opts.num_bins = N_MELS
    fb = knf.OnlineFbank(opts)
    fb.accept_waveform(SAMPLE_RATE, (audio16k * 32768.0).tolist())
    fb.input_finished()
    feats = np.stack([fb.get_frame(i) for i in range(fb.num_frames_ready)])
    return feats - feats.mean(axis=0, keepdims=True)


class WespeakerEmbedder:
    name = "wespeaker-resnet34"
    dim = 256

    def __init__(self, model_path: str | Path | None = None):
        path = Path(model_path) if model_path else DEFAULT_MODEL
        if not path.exists():
            raise FileNotFoundError(f"{path} missing; run scripts/fetch_models.py --only wespeaker")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = N_THREADS
        self._sess = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self._in = self._sess.get_inputs()[0].name

    def embed(self, audio16k: np.ndarray) -> np.ndarray:
        x = np.asarray(audio16k, np.float32)
        if len(x) < MIN_SAMPLES:
            x = np.pad(x, (0, MIN_SAMPLES - len(x)), mode="wrap" if len(x) else "constant")
        feats = _fbank(x)[None].astype(np.float32)
        (emb,) = self._sess.run(None, {self._in: feats})
        return l2_normalize(emb[0])
