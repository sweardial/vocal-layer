"""Smoke: SpeechBrain ECAPA embedder (SKIPs if the `ecapa` extra or the model is missing)."""

import sys

import numpy as np

from voiceroom import synth
from voiceroom.embedders import get_embedder

try:
    from voiceroom.embedders.ecapa import DEFAULT_MODEL
except ImportError as e:
    print(f"SKIP: ecapa extra not installed ({e}); uv sync --extra ecapa")
    sys.exit(77)

if not DEFAULT_MODEL.exists():
    print("SKIP: run scripts/fetch_models.py --only ecapa")
    sys.exit(77)

emb = get_embedder("ecapa-voxceleb")
x = synth.utterance(1, 5.0, seed=0)
v = emb.embed(x)
assert emb.dim == 192 and v.shape == (192,) and v.dtype == np.float32
assert abs(np.linalg.norm(v) - 1.0) < 1e-5
assert np.allclose(v, emb.embed(x.copy()), atol=1e-6), "non-deterministic"
assert np.isfinite(emb.embed(x[:1600])).all()
