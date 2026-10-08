"""Smoke: bench on the synthetic corpus with the toy embedder; check report sections and EER."""

import re
import tempfile
import time
from pathlib import Path

import numpy as np

from voiceroom.bench import metrics as M
from voiceroom.bench.__main__ import main

# metric sanity on hand-made scores
s = np.array([0.9, 0.8, 0.7, 0.4, 0.3, 0.2])
y = np.array([1, 1, 1, 0, 0, 0], bool)
assert M.eer(s, y)[0] == 0.0
assert M.far_frr(s, y, 0.5) == (0.0, 0.0) and M.far_frr(s, y, 0.0)[0] == 1.0
assert M.far_frr(s, y, 1.0)[1] == 1.0
assert M.far_frr(s, y[::-1], 0.5) == (1.0, 1.0)
assert M.far_frr(s, ~y, 0.35)[0] == 1.0
assert np.isclose(M.eer(np.array([0.5] * 4), np.array([1, 1, 0, 0], bool))[0], 0.5)
assert M.far_frr(s, y, M.threshold_for_far(s, y, 0.0))[0] == 0.0
assert [M.bucket_of(d) for d in (0.5, 1.0, 2.0, 3.9, 4.0)] == [
    "<1 s",
    "1-2 s",
    "2-4 s",
    "2-4 s",
    ">=4 s",
]

t0 = time.monotonic()
with tempfile.TemporaryDirectory() as d:
    assert main(["--corpus", "synth", "--embedder", "toy", "--out", d]) == 0
    text = (Path(d) / "bench-toy.md").read_text()
dt = time.monotonic() - t0
for sec in (
    "## EER",
    "## FAR / FRR",
    "## Results by utterance length",
    "## Latency",
    "## Real-time",
):
    assert sec in text, sec
for h in ("dataset", "seed", "config"):
    assert f"**{h}**" in text, h
for b in ("<1 s", "1-2 s", "2-4 s", ">=4 s"):
    assert f"| {b} |" in text, b
eer = float(re.search(r"EER \*\*([\d.]+)%", text).group(1)) / 100
assert 0.0 <= eer <= 0.5, eer
assert "p50" in text and "p95" in text and "RTF" in text
assert dt < 10, dt
print(f"bench ok: EER={eer:.2%} in {dt:.1f}s")
