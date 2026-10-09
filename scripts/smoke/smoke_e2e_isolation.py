"""Smoke: 3 synthetic conversations (owner + 2 others) through the full isolation pipeline."""

import asyncio
import time

import numpy as np

from voiceroom.adapters import MockProvider
from voiceroom.audio import iter_frames
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import make_conversation, split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.matcher import Matcher
from voiceroom.pipeline import Pipeline
from voiceroom.policy import IsolationPolicy, Verdict
from voiceroom.segmenter import Segmenter
from voiceroom.vad import FRAME_SIZE, EnergyVAD

corpus = SynthCorpus(3, seed=1, utts_per_speaker=12)
spks = corpus.speakers()
owner = spks[0]
emb = get_embedder("toy")
enroll_utts, _ = split_utterances(corpus, spks, n_enroll=6)
sigs = [enroll(s, np.concatenate([u.load() for u in enroll_utts[s]]), emb) for s in spks]
# Toy embedder: threshold midway between genuine and impostor scores on 1 s held-out crops.
_, held = split_utterances(corpus, spks, n_enroll=6)
cal = Matcher(sigs, threshold=0.0)
gen, imp = [], []
for s in spks:
    for u in held[s]:
        sc = cal.identify_embedding(emb.embed(u.load()[:16000])).scores
        gen.append(sc[s])
        imp += [v for k, v in sc.items() if k != s]
threshold = (float(np.percentile(gen, 5)) + float(np.percentile(imp, 95))) / 2
print(
    f"threshold={threshold:.3f} genuine p5={np.percentile(gen, 5):.3f} "
    f"impostor p95={np.percentile(imp, 95):.3f}"
)

owner_total = owner_ok = non_owner_committed = 0
records = []
for seed in (11, 12, 13):
    conv = make_conversation(
        corpus, spks, n_turns=10, pause_range=(0.6, 1.2), snr_db=None, seed=seed, n_enroll=6
    )
    provider = MockProvider()
    pipe = Pipeline(
        EnergyVAD(),
        Segmenter(),
        Matcher(sigs, threshold=threshold, embedder=emb),
        IsolationPolicy(owner, threshold, early_score_buffer=0.02, early_margin=0.02),
        provider,
        max_pending=1000,
    )
    asyncio.run(pipe.run(iter_frames(conv.audio, FRAME_SIZE)))
    assert len(pipe.records) == len(provider.turns) + sum(
        r.verdict is Verdict.REJECT for r in pipe.records
    )
    committed = [r for r in pipe.records if r.verdict is Verdict.ACCEPT]
    assert len(committed) == len(provider.turns)
    for r in pipe.records:  # ground truth speaker of each turn = segment it overlaps most
        seg = max(conv.segments, key=lambda s: min(s[1], r.t_end) - max(s[0], r.t_start))
        if seg[2] != owner and r.verdict is Verdict.ACCEPT:
            non_owner_committed += 1
    for a, b, spk in conv.segments:
        if spk == owner:
            owner_total += 1
            owner_ok += any(
                r.verdict is Verdict.ACCEPT and r.t_start < b and r.t_end > a for r in pipe.records
            )
    assert all(t.speaker_label == owner for t in provider.turns)
    records += pipe.records

# Late path: no early identification (turns shorter than identify_after_s), decided at TurnEnd.
late = Pipeline(
    EnergyVAD(),
    Segmenter(identify_after_s=1e3),
    Matcher(sigs, threshold=threshold, embedder=emb),
    IsolationPolicy(owner, threshold),
    MockProvider(),
    max_pending=1000,
)
asyncio.run(late.run(iter_frames(conv.audio, FRAME_SIZE)))
assert late.records and not any(r.early for r in late.records)
assert [r.verdict is Verdict.ACCEPT for r in late.records] == [
    r.verdict is Verdict.ACCEPT for r in pipe.records
]


def make_pipe(provider, segmenter=None, **kw):
    return Pipeline(
        EnergyVAD(),
        segmenter or Segmenter(),
        Matcher(sigs, threshold=threshold, embedder=emb),
        IsolationPolicy(owner, threshold, early_score_buffer=0.02, early_margin=0.02),
        provider,
        **({"max_pending": 1000} | kw),  # offline source is faster than real time
    )


def seg_audio(owner_side: bool, max_s: float | None = None) -> np.ndarray:
    a, b, _ = next(g for g in conv.segments if (g[2] == owner) == owner_side)
    if max_s is not None:
        b = min(b, a + max_s)
    return conv.audio[int(a * 16000) : int(b * 16000)]


# Owner speech immediately followed (no pause) by non-owner speech must not be committed,
# whether the non-owner part is long or short (0.5 s: under the 1 s minimum decision length).
gap = np.zeros(16000, np.float32)
for suffix_s in (None, 0.5):
    mixed = np.concatenate([seg_audio(True), seg_audio(False, suffix_s), gap])
    mix = make_pipe(MockProvider())
    asyncio.run(mix.run(iter_frames(mixed, FRAME_SIZE)))
    assert len(mix.records) == 1, mix.records
    assert mix.records[0].verdict is Verdict.REJECT, mix.records


class FlakyProvider(MockProvider):
    """Fails the first send, then (optionally) stalls every send."""

    def __init__(self, stall_s: float = 0.0, fail_first: bool = True):
        super().__init__()
        self.calls, self.stall_s, self.fail_first = 0, stall_s, fail_first

    async def send_turn(self, audio16k, speaker_label):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("secret-payload")
        await asyncio.sleep(self.stall_s)
        await super().send_turn(audio16k, speaker_label)


n_turns = len(pipe.records)
# Failure then recovery: the failed turn is recorded (fixed code, no payload), later ones commit.
fl = make_pipe(FlakyProvider())
twice = np.concatenate(
    [conv.audio, gap, conv.audio]
)  # first owner send fails, a later one succeeds
asyncio.run(fl.run(iter_frames(twice, FRAME_SIZE)))
assert len(fl.records) == 2 * n_turns
errs = [r.error for r in fl.records if r.error]
assert errs == ["send_error"], errs
assert sum(r.verdict is Verdict.ACCEPT for r in fl.records) == len(fl.provider.turns) > 0

# Stalled provider with a bounded queue: sends time out, excess turns are dropped, every turn
# is accounted for, and the run still ends.
slow = make_pipe(FlakyProvider(stall_s=5.0, fail_first=False), max_pending=1, send_timeout_s=0.05)
asyncio.run(slow.run(iter_frames(twice, FRAME_SIZE)))
assert len(slow.records) == 2 * n_turns, (len(slow.records), n_turns)
codes = {r.error for r in slow.records if r.error}
assert {"send_timeout", "overflow"} <= codes, codes
assert not slow.provider.turns

# Short continuation (0.16 s) after the early check, and the no-early fallback path with the
# policy's default (wide) buffers: neither may forward the non-owner audio.
for kw in ({"early_score_buffer": 0.02, "early_margin": 0.02}, {}):
    for suffix_s, tail in ((0.16, gap), (0.5, gap), (0.064, None), (0.096, None), (0.128, None)):
        parts = [seg_audio(True, 1.024), seg_audio(False, suffix_s)]  # tail None = ends at EOF
        mixed = np.concatenate(parts + ([tail] if tail is not None else []))
        pl = make_pipe(MockProvider())
        pl.policy = IsolationPolicy(owner, threshold, **kw)
        asyncio.run(pl.run(iter_frames(mixed, FRAME_SIZE)))
        assert len(pl.records) == 1 and pl.records[0].verdict is Verdict.REJECT, pl.records
        assert not pl.provider.turns, (kw, suffix_s)
assert not make_pipe(MockProvider()).policy.early_score_buffer > 0.1  # smoke buffers are narrow


# Blocked matcher: identification work stays bounded, run ends, every turn is accounted for.
class SlowMatcher(Matcher):
    def identify(self, audio16k):
        time.sleep(0.15)
        return super().identify(audio16k)


slow_m = make_pipe(MockProvider(), max_pending=1, identify_timeout_s=0.05, max_inflight_identify=2)
slow_m.matcher = SlowMatcher(sigs, threshold=threshold, embedder=emb)
asyncio.run(slow_m.run(iter_frames(twice, FRAME_SIZE)))
assert len(slow_m.records) == 2 * n_turns, (len(slow_m.records), n_turns)
assert slow_m.peak_inflight <= 2, slow_m.peak_inflight
assert {"overflow", "identify_timeout"} <= {r.error for r in slow_m.records}
assert not slow_m.provider.turns or all(
    r.error is None for r in slow_m.records if r.verdict is Verdict.ACCEPT
)

lat = np.array([r.latency_s for r in records])
comp = np.array([r.compute_s for r in records])
early = sum(r.early for r in records)
print(
    f"turns={len(records)} early={early} owner {owner_ok}/{owner_total} "
    f"non-owner committed={non_owner_committed}"
)
print(
    f"end-of-speech->commit latency p50={np.percentile(lat, 50) * 1000:.0f} ms "
    f"p95={np.percentile(lat, 95) * 1000:.0f} ms "
    f"(compute-only p50={np.percentile(comp, 50) * 1000:.0f} ms "
    f"p95={np.percentile(comp, 95) * 1000:.0f} ms)"
)
assert non_owner_committed == 0
assert owner_ok >= 0.9 * owner_total, (owner_ok, owner_total)
print("e2e isolation ok")
