"""Smoke: toy embedder separates synthetic speakers; unit-norm, deterministic, registry."""

import numpy as np

from voiceroom import synth
from voiceroom.embedders import embed_batch, get_embedder

emb = get_embedder("toy")
assert emb.name == "toy" and emb.dim > 0
try:
    get_embedder("nope")
    raise SystemExit("expected ValueError")
except ValueError:
    pass

speakers = [1, 2, 3, 4]
E = {s: [emb.embed(synth.utterance(s, 2.0, seed=k)) for k in range(3)] for s in speakers}

same, diff = [], []
for a in speakers:
    for i in range(3):
        v = E[a][i]
        assert v.shape == (emb.dim,) and v.dtype == np.float32
        assert abs(np.linalg.norm(v) - 1.0) < 1e-5
        for b in speakers:
            for j in range(3):
                if (a, i) < (b, j):
                    (same if a == b else diff).append(float(v @ E[b][j]))

ms, md = np.mean(same), np.mean(diff)
print(f"same={ms:.3f} diff={md:.3f} margin={ms - md:.3f}")
assert ms - md >= 0.1, (ms, md)

x = synth.utterance(1, 2.0, seed=0)
assert np.array_equal(emb.embed(x), emb.embed(x.copy()))
assert np.array_equal(embed_batch(emb, [x, x])[0], emb.embed(x))
z = emb.embed(np.zeros(100, np.float32))
assert np.isfinite(z).all() and abs(np.linalg.norm(z) - 1.0) < 1e-5
