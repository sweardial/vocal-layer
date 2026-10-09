"""Smoke: noise-augmented enrollment adds chunks; AS-norm Matcher ranks the owner first."""

import tempfile
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet

from voiceroom import synth
from voiceroom.cohort import Cohort, estimate_score_scale, load_default
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.matcher import Match, Matcher
from voiceroom.policy import CollaborationPolicy, IsolationPolicy

emb = get_embedder("toy")
KEY = Fernet.generate_key()
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

# cohort file round-trip + z-unit rescale of the policies' early buffers
scale = estimate_score_scale(cohort / np.linalg.norm(cohort, axis=1, keepdims=True))
assert scale > 1.0, scale  # cosine spread is < 1, so z-units per cosine > 1
co = Cohort(cohort, emb.name, thr, 100, scale)
with tempfile.TemporaryDirectory() as d:
    assert load_default(emb.name, KEY, Path(d)) is None  # absent -> AS-norm stays off
    co.save(Path(d) / f"cohort-{emb.name}.npz", KEY)
    back = load_default(emb.name, KEY, Path(d))
assert back is not None and np.allclose(back.embeddings, cohort, atol=1e-6)
assert (back.threshold, back.score_scale) == (thr, scale)
cm = Matcher(sigs, cohort=back)  # threshold comes from the cohort, not config
assert cm.threshold == thr and cm.score_scale == scale
assert (
    cm.identify_embedding(probe["a"]).name == "a" and cm.identify_embedding(probe["x"]).name is None
)
try:
    Matcher(sigs, cohort=Cohort(cohort, "other", thr))
    raise SystemExit("expected ValueError for cohort/embedder mismatch")
except ValueError:
    pass

# a 0.15 cosine lead is clear (>= 0.1 buffer) in cosine units but not in z-units once scaled
big = Match("a", thr + 0.5, 1.0, "a", {"a": thr + 0.5, "b": thr - 0.5})
assert IsolationPolicy("a", thr).decide(big, final=False).verdict.value == "accept"
iso = IsolationPolicy("a", thr)
iso.bind_matcher(cm)
assert iso.decide(big, final=False).verdict.value == "undecided", "buffer not rescaled"
col = CollaborationPolicy(["a", "b"])
col.bind_matcher(cm)
assert col.score_scale == scale and col.decide(big, final=False).verdict.value == "undecided"
print("cohort ok")
