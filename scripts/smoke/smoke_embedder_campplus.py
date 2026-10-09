"""Smoke: CAM++ ONNX embedder (SKIPs if the model is not fetched)."""

import sys
import time

import numpy as np

from voiceroom import synth
from voiceroom.embedders import get_embedder
from voiceroom.embedders.campplus import DEFAULT_MODEL

if not DEFAULT_MODEL.exists():
    print("SKIP: run scripts/fetch_models.py --only campplus")
    sys.exit(77)

emb = get_embedder("campplus-en-voxceleb")
x = synth.utterance(1, 5.0, seed=0)
v = emb.embed(x)
assert emb.dim == 512 and v.shape == (512,) and v.dtype == np.float32
assert abs(np.linalg.norm(v) - 1.0) < 1e-5
assert np.array_equal(v, emb.embed(x.copy())), "non-deterministic"

t = time.perf_counter()
for _ in range(3):
    emb.embed(x)
rtf = (time.perf_counter() - t) / 3 / 5.0
print(f"RTF={rtf:.3f}")
assert rtf < 0.2, rtf

short = emb.embed(x[:1600])
assert np.isfinite(short).all()

a1, a2 = emb.embed(synth.utterance(1, 4.0, seed=1)), emb.embed(synth.utterance(1, 4.0, seed=2))
b = emb.embed(synth.utterance(2, 4.0, seed=3))
print(f"same={a1 @ a2:.3f} diff={a1 @ b:.3f}")
assert a1 @ a2 > a1 @ b, "same-speaker should score above different-speaker"
