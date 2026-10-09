"""3D-Speaker CAM++ (VoxCeleb, English 16 kHz) on onnxruntime, sherpa-onnx export.

Front end matches 3D-Speaker: waveform in [-1, 1], Kaldi fbank with 80 mel bins, dither 0, default
(povey) window, then per-utterance mean normalisation. Model: Apache-2.0, see models/README.
"""

import os
from pathlib import Path

import kaldi_native_fbank as knf
import numpy as np
import onnxruntime as ort

from voiceroom.audio import SAMPLE_RATE
from voiceroom.embedders.base import l2_normalize

DEFAULT_MODEL = (
    Path(__file__).resolve().parents[3]
    / "models"
    / "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
)
N_MELS = 80
N_THREADS = max(1, min(4, os.cpu_count() or 1))
MIN_SAMPLES = SAMPLE_RATE  # pad shorter inputs to 1 s, as the wespeaker embedder does


def _fbank(audio16k: np.ndarray) -> np.ndarray:
    opts = knf.FbankOptions()
    opts.frame_opts.samp_freq = SAMPLE_RATE
    opts.frame_opts.dither = 0.0
    opts.frame_opts.snip_edges = True
    opts.use_energy = False
    opts.mel_opts.num_bins = N_MELS
    fb = knf.OnlineFbank(opts)
    fb.accept_waveform(SAMPLE_RATE, audio16k.tolist())
    fb.input_finished()
    feats = np.stack([fb.get_frame(i) for i in range(fb.num_frames_ready)])
    return feats - feats.mean(axis=0, keepdims=True)


class CamPlusEmbedder:
    name = "campplus-en-voxceleb"
    dim = 512

    def __init__(self, model_path: str | Path | None = None):
        path = Path(model_path) if model_path else DEFAULT_MODEL
        if not path.exists():
            raise FileNotFoundError(f"{path} missing; run scripts/fetch_models.py --only campplus")
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
