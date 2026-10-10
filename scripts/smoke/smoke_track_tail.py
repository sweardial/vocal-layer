"""Smoke: a 1 s stranger tail after the owner is rejected on a verified track; dips are not."""

from voiceroom.config import TRACK_LOW_RUN
from voiceroom.matcher import Match
from voiceroom.pipeline import HOP_FRAMES
from voiceroom.policy import TrackPolicy
from voiceroom.vad import FRAME_S

p = TrackPolicy("verified", ["a", "b"], "a", "drop", embedder="wespeaker-resnet34")
hi, lo = p.verify_threshold + 0.2, p.verify_threshold - 0.25


def win(score):
    return Match(None, score, 0.0, best="a", scores={"a": score})


hop_s = HOP_FRAMES * FRAME_S
n_tail = int(1.0 / hop_s)  # windows fully inside a 1 s tail (slightly more once overlap counts)
owner = [win(hi)] * 4
assert p.windows_rule(owner + [win(lo)] * n_tail, "a", False), f"{n_tail}-window tail accepted"
mid = [win(p.verify_threshold - 0.15)] * 2  # ~0.75 s of a stranger scoring only a bit low
assert p.windows_rule(owner + mid, "a", False), "short moderate tail accepted"
assert p.windows_rule(owner + [win(lo)] + owner, "a", False) is None, "one dip rejected the owner"
assert p.windows_rule(owner + [win(lo)] + owner + [win(lo)] + owner, "a", False) is None
assert p.windows_rule(owner, "a", False) is None
print(f"ok: tail of {n_tail} under-floor windows (1 s) rejected, LOW_RUN={TRACK_LOW_RUN}")
