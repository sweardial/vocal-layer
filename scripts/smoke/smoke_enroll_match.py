"""Smoke: enroll 3 synthetic speakers, identify held-out utterances, reject an unenrolled one."""

import numpy as np

from voiceroom import synth
from voiceroom.embedders import get_embedder
from voiceroom.enroll import EnrollmentError, enroll
from voiceroom.matcher import Matcher

emb = get_embedder("toy")
names = {1: "a", 2: "b", 3: "c"}
sigs = [enroll(n, synth.utterance(s, 24.0, seed=100 + s), emb) for s, n in names.items()]
assert all(str(float(s.centroid[0]))[:6] not in repr(s) for s in sigs), repr(sigs[0])
assert "array" not in repr(sigs[0]) and sigs[0] != sigs[1] and hash(sigs[0])
print(sigs[0])

try:
    enroll("x", synth.utterance(1, 5.0), emb)
    raise SystemExit("expected EnrollmentError for short audio")
except EnrollmentError:
    pass
try:
    enroll("x", np.zeros(16000 * 20, np.float32), emb)
    raise SystemExit("expected EnrollmentError for silence")
except EnrollmentError:
    pass

# Calibrate threshold from enrolled chunk scores: below the lowest genuine own-chunk score.
own = [float(s.chunks[i] @ s.centroid) for s in sigs for i in range(len(s.chunks))]
thr = float(np.percentile(own, 5)) - 0.1
m = Matcher(sigs, threshold=thr, embedder=emb)

try:
    enroll("x", np.random.default_rng(0).standard_normal(16000 * 20).astype(np.float32) * 0.1, emb)
    raise SystemExit("expected EnrollmentError for noise")
except EnrollmentError:
    pass
try:
    m.identify_embedding(np.ones(5, np.float32))
    raise SystemExit("expected ValueError for bad embedding shape")
except ValueError:
    pass
assert m.identify_embedding(sigs[1].centroid).name == "b"

correct = total = 0
for s, n in names.items():
    for k in range(10):
        r = m.identify(synth.utterance(s, 2.0, seed=k))
        assert set(r.scores) == set(names.values())
        correct += r.name == n
        total += 1
acc = correct / total
rej = sum(m.identify(synth.utterance(4, 2.0, seed=k)).name is None for k in range(20)) / 20
print(f"threshold={thr:.3f} accuracy={acc:.0%} unenrolled_rejected={rej:.0%}")
assert acc >= 0.9, acc
assert rej >= 0.5, rej
