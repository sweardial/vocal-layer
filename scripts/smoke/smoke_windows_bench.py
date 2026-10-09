"""Smoke: the window-continuity calibration bench on SynthCorpus with the toy embedder."""

import tempfile

from voiceroom.bench import windows as W
from voiceroom.embedders import get_embedder
from voiceroom.eval.corpus import SynthCorpus

emb = get_embedder("toy")
corpus = SynthCorpus(8, seed=2, utts_per_speaker=8, duration_range=(8.0, 10.0))
res = W.collect(corpus, emb, 2, 0, W.WIN_FRAMES, W.WIN_FRAMES // 2)
assert len(res["owner"]) == 4 and len(res[(1.0, "stranger")]) == 2
text = W.render("toy", res, 0.35, [0.1], {"trials": 2})
assert "owner FR" in text and "| 0.05 |" in text and "leak all" in text
ok, n = W._passes(res["owner"], 0.35, 1.0, 10.0)
assert n == 4
assert W.main(["--data", tempfile.mkdtemp(), "--out", tempfile.mkdtemp()]) == 2
print("windows bench ok")
