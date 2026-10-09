"""Smoke: noise-augmented enrollment adds chunks; AS-norm Matcher ranks the owner first."""

import numpy as np

from voiceroom import synth
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.matcher import Matcher

emb = get_embedder("toy")
audio = synth.utterance(1, 24.0, seed=100)
plain = enroll("a", audio, emb, ())
aug = enroll("a", audio, emb, (20.0, 10.0))
assert len(aug.chunks) == 3 * len(plain.chunks), (len(aug.chunks), len(plain.chunks))
n = len(plain.chunks)
assert not np.allclose(aug.chunks[n : 2 * n], plain.chunks)  # noise changed the embeddings
assert np.isclose(np.linalg.norm(aug.centroid), 1.0)

sigs = [enroll(n, synth.utterance(s, 24.0, seed=100 + s), emb, ()) for s, n in ((1, "a"), (2, "b"))]
cohort = np.stack([emb.embed(synth.utterance(s, 4.0, seed=900 + s)) for s in range(10, 40)])
probe = {k: emb.embed(synth.utterance(sp, 4.0, seed=sd)) for k, sp, sd in
         (("a", 1, 500), ("b", 2, 501), ("x", 3, 502), ("y", 4, 503))}  # fmt: skip
raw = Matcher(sigs, threshold=1.0, cohort=cohort)
sc = {k: raw.identify_embedding(v).scores for k, v in probe.items()}
# calibrate on the synthetic cohort: between the strangers' best and the owners' own scores
thr = (max(max(sc["x"].values()), max(sc["y"].values())) + min(sc["a"]["a"], sc["b"]["b"])) / 2
assert max(sc["x"].values()) < min(sc["a"]["a"], sc["b"]["b"]), sc
m = Matcher(sigs, threshold=thr, cohort=cohort)
own, oth = m.identify_embedding(probe["a"]), m.identify_embedding(probe["b"])
assert (own.name, oth.name) == ("a", "b"), (own, oth)
for k in ("x", "y"):
    assert m.identify_embedding(probe[k]).name is None, k

# degenerate cohorts fail closed: duplicate rows -> reject everything; bad rows -> ValueError
dim = len(probe["a"])
e = np.eye(dim, dtype=np.float32)
dsig = [
    type(sigs[0])("a", emb.name, e[0], e[:1], 10.0),
    type(sigs[0])("b", emb.name, e[1], e[1:2], 10.0),
]
for bad in (np.stack([e[2], e[2]]), np.stack([e[2], e[2] + 1e-5 * e[3]])):
    try:
        dm = Matcher(dsig, threshold=2.88, cohort=bad)
    except ValueError:
        continue  # rejected at construction: fine
    p = 0.1 * e[0] + np.sqrt(0.99) * e[1]
    r = dm.identify_embedding(p.astype(np.float32))
    assert r.name is None, r
for bad in (np.zeros((2, dim)), np.full((2, dim), np.nan)):
    try:
        Matcher(dsig, threshold=2.88, cohort=bad)
        raise SystemExit("expected ValueError for bad cohort")
    except ValueError:
        pass
print(f"noise-robust ok: own={own.score:.2f} thr={thr:.2f}")
