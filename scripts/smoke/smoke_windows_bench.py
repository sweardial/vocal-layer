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
from voiceroom.matcher import Match  # noqa: E402
from voiceroom.policy import IsolationPolicy  # noqa: E402

good = Match("o", 0.9, 0.5, "o", {"o": 0.9})
bad = Match(None, 0.1, 0.0, "x", {"o": 0.1, "x": 0.1})
pol = IsolationPolicy("o", 0.35, window_drop=0.3, window_bad_run=3)
assert not pol.windows_ok([bad, None, bad])  # all scored windows bad, pause-separated
frn = Match("x", 0.9, 0.5, "x", {"o": 0.1, "x": 0.9})
assert not pol.windows_ok([good, good, frn, good, good])  # one confident foreign window
from voiceroom.policy import CollaborationPolicy  # noqa: E402

col = CollaborationPolicy(["alice"], unknown="label", threshold=0.35, window_drop=0.3)
assert col.windows_ok([bad], "Unknown")  # standalone, no bind_matcher
assert pol.windows_ok([good, bad, bad, good]) and not pol.windows_ok([good, bad, bad, bad, good])
assert W.main(["--data", tempfile.mkdtemp(), "--out", tempfile.mkdtemp()]) == 2
print("windows bench ok")
