"""Smoke: the window-continuity calibration bench on SynthCorpus with the toy embedder."""

import asyncio
import tempfile

import numpy as np

from voiceroom.bench import windows as W
from voiceroom.embedders import get_embedder
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.pipeline import Pipeline
from voiceroom.segmenter import TurnEnd
from voiceroom.vad import FRAME_SIZE

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
mild = Match(None, 0.3, 0.0, "x", {"o": 0.3, "x": 0.31})  # bad, but not clearly someone else
assert pol.windows_ok([good, mild, mild, good])
assert not pol.windows_ok([good, mild, mild, mild, good])
assert not pol.windows_ok([good, bad, bad, good]) and pol.windows_ok([good, bad, None, bad, good])


# Turns under CONTINUITY_MIN_S use the lenient `short` rule; the flag reaches the policy.
low = Match(None, 0.05, 0.0, "o", {"o": 0.05})  # owner-only enrollment: best is the owner
assert not pol.windows_ok([good, low, low], short=True) and pol.windows_ok(
    [good, low, good], short=True
)


class _Stub:
    class policy:
        @staticmethod
        def windows_rule(matches, label=None, short=False):
            return None if short else "stub"

        @staticmethod
        def describe_windows(matches, label, short, rule):
            return rule

    async def _identify(self, audio):
        return bad


def _ok(seconds):
    n = int(seconds * 16000) // FRAME_SIZE * FRAME_SIZE
    ev = TurnEnd(np.zeros(n, np.float32), 0.0, seconds)
    return asyncio.run(Pipeline._continuity_ok(_Stub(), ev, {}))


assert _ok(1.5) and not _ok(3.0)
assert pol.windows_ok([good, mild, mild, mild, good], short=True)  # doubtful run: not enough
assert pol.windows_ok([good, frn, good], short=True)  # lone foreign window tolerated
assert not pol.windows_ok([good, frn, frn], short=True) and not pol.windows_ok(
    [good, bad, bad], short=True
)
assert W.main(["--data", tempfile.mkdtemp(), "--out", tempfile.mkdtemp()]) == 2
print("windows bench ok")
