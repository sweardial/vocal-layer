"""SpeechBrain ECAPA-TDNN (VoxCeleb) embedder. Needs the optional `ecapa` extra (torch CPU).

Built directly from speechbrain modules (Fbank -> sentence mean norm -> ECAPA_TDNN), mirroring
`EncoderClassifier.encode_batch` of speechbrain/spkrec-ecapa-voxceleb (hyperparams.yaml). We skip
its pretrained/fetch machinery and load audio ourselves (numpy in), so torchaudio backends are
never used. Weights: Apache-2.0, see models/README (written by scripts/fetch_models.py).
"""

from pathlib import Path

import numpy as np
import torch
from speechbrain.lobes.features import Fbank
from speechbrain.lobes.models.ECAPA_TDNN import ECAPA_TDNN
from speechbrain.processing.features import InputNormalization

from voiceroom.audio import SAMPLE_RATE
from voiceroom.embedders.base import l2_normalize

DEFAULT_MODEL = (
    Path(__file__).resolve().parents[3] / "models" / "ecapa_voxceleb_embedding_model.ckpt"
)
N_MELS = 80
MIN_SAMPLES = SAMPLE_RATE  # pad very short clips to 1 s, as for the other embedders


class EcapaEmbedder:
    name = "ecapa-voxceleb"
    dim = 192

    def __init__(self, model_path: str | Path | None = None):
        path = Path(model_path) if model_path else DEFAULT_MODEL
        if not path.exists():
            raise FileNotFoundError(f"{path} missing; run scripts/fetch_models.py --only ecapa")
        torch.set_num_threads(1)
        self._fbank = Fbank(n_mels=N_MELS)
        self._norm = InputNormalization(norm_type="sentence", std_norm=False)
        self._model = ECAPA_TDNN(
            input_size=N_MELS,
            channels=[1024, 1024, 1024, 1024, 3072],
            kernel_sizes=[5, 3, 3, 3, 1],
            dilations=[1, 2, 3, 4, 1],
            attention_channels=128,
            lin_neurons=self.dim,
        )
        self._model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
        for m in (self._fbank, self._norm, self._model):
            m.eval()

    def embed(self, audio16k: np.ndarray) -> np.ndarray:
        x = np.asarray(audio16k, np.float32)
        if len(x) < MIN_SAMPLES:
            x = np.pad(x, (0, MIN_SAMPLES - len(x)), mode="wrap" if len(x) else "constant")
        wav = torch.from_numpy(np.ascontiguousarray(x))[None]
        lens = torch.ones(1)
        with torch.inference_mode():
            feats = self._norm(self._fbank(wav), lens)
            emb = self._model(feats, lens)
        return l2_normalize(emb.reshape(-1).numpy())
