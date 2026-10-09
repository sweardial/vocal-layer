"""Pipeline benchmark: SileroVAD + a real embedder + LibriSpeech conversations at realtime pace.

    python -m voiceroom.bench.pipeline [--embedder NAME] [--conversations N] [--out reports]

Each conversation has 3 enrolled speakers (the first is the owner) and 1 stranger. It is streamed
twice at realtime pacing with MockProvider: through the isolation pipeline (owner FR, non-owner
FA, latency) and through the collaboration pipeline (attribution accuracy). Writes
reports/pipeline-<embedder>.md.
"""

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from voiceroom.adapters import MockProvider
from voiceroom.audio import iter_frames
from voiceroom.bench.run import enroll_speakers
from voiceroom.cohort import Cohort, load_default
from voiceroom.config import MIN_DECISION_S, threshold_for, window_bad_run_for, window_drop_for
from voiceroom.embedders import get_embedder
from voiceroom.eval.conversations import Conversation, make_conversation
from voiceroom.eval.corpus import LibriSpeechCorpus, Utterance
from voiceroom.matcher import Matcher
from voiceroom.pipeline import Pipeline, TurnRecord
from voiceroom.policy import UNKNOWN_LABEL, CollaborationPolicy, IsolationPolicy, Verdict
from voiceroom.segmenter import Segmenter
from voiceroom.store import load_key
from voiceroom.vad import DEFAULT_MODEL, FRAME_S, FRAME_SIZE, SileroVAD

MIN_LATENCY_N = 20  # fewer samples than this do not support a latency verdict
TARGET_S = 0.7  # end of speech -> commit (SPEC)
BUCKETS = ((0.0, 1.0), (1.0, 2.0), (2.0, 4.0), (4.0, float("inf")))
N_ENROLLED = 3
N_ENROLL_UTTS = 5


class CappedCorpus:
    """Corpus view without utterances longer than `max_s` (keeps smoke conversations short)."""

    def __init__(self, base: LibriSpeechCorpus, max_s: float):
        self.base, self.max_s = base, max_s

    def speakers(self) -> list[str]:
        return self.base.speakers()

    def utterances(self, speaker: str) -> list[Utterance]:
        return [u for u in self.base.utterances(speaker) if u.duration <= self.max_s]


async def paced_frames(audio: np.ndarray, speed: float = 1.0):
    """512-sample frames delivered at (speed x) realtime, as a live mic would."""
    t0 = time.monotonic()
    for i, frame in enumerate(iter_frames(audio, FRAME_SIZE)):
        delay = t0 + i * FRAME_S / speed - time.monotonic()
        await asyncio.sleep(max(delay, 0.0))
        yield frame


def _overlap(seg, rec: TurnRecord) -> float:
    return min(seg[1], rec.t_end) - max(seg[0], rec.t_start)


PREROLL_S, TAIL_S = 0.2, 0.1  # Segmenter defaults: audio sent extends this far around t_start/t_end


def _leaks(seg, rec: TurnRecord) -> bool:
    """Any of the segment is inside the audio sent for this turn (pre-roll and tail included)."""
    return min(seg[1], rec.t_end + TAIL_S) > max(seg[0], rec.t_start - PREROLL_S)


def _covered(seg, rec: TurnRecord) -> bool:
    """The turn carries a meaningful part of the segment (>= 0.3 s or half of it)."""
    return _overlap(seg, rec) >= min(0.3, 0.5 * (seg[1] - seg[0]))


EXCLUDE: frozenset[str] = frozenset()  # cohort speakers: never drawn, so A/B runs share convs
COHORT: Cohort | None = None  # set by --cohort: AS-norm scoring with the shipped cohort


def _threshold(name):
    return COHORT.threshold if COHORT else threshold_for(name)


def stream(conv, sigs, emb, policy, speed, provider=None):
    provider = provider or MockProvider()
    pipe = Pipeline(
        SileroVAD(),
        Segmenter(),
        Matcher(sigs, threshold=_threshold(emb.name), embedder=emb, cohort=COHORT),
        policy,
        provider,
        max_pending=1000,
        isolation="thread",
    )
    asyncio.run(pipe.run(paced_frames(conv.audio, speed)))
    return list(pipe.records), provider


@dataclass
class Stats:
    latencies: list[float] = field(default_factory=list)  # every record (commit or drop)
    commit_latencies: list[float] = field(default_factory=list)
    endpoint: list[float] = field(default_factory=list)
    compute: list[float] = field(default_factory=list)
    early: int = 0
    records: int = 0
    segments: int = 0  # ground-truth segments (isolation pass)
    errors: int = 0
    owner: dict[int, list[int]] = field(default_factory=dict)  # bucket -> [rejected, total]
    non_owner: list[int] = field(default_factory=lambda: [0, 0])  # [forwarded, total]
    attrib: list[int] = field(default_factory=lambda: [0, 0])  # [correct, total]
    attrib_unknown: list[int] = field(default_factory=lambda: [0, 0])
    attrib_enrolled: list[int] = field(default_factory=lambda: [0, 0])


def _bucket(dur: float) -> int:
    return next(i for i, (lo, hi) in enumerate(BUCKETS) if lo <= dur < hi)


def score_isolation(st: Stats, conv: Conversation, records: list[TurnRecord], owner: str) -> None:
    forwarded, leaked = set(), set()  # owner: covered; others: any positive overlap
    for r in records:
        st.records += 1
        st.errors += r.error is not None
        st.early += r.early
        st.latencies.append(r.latency_s)
        st.endpoint.append(r.endpoint_s)
        st.compute.append(r.compute_s)
        if r.verdict is Verdict.ACCEPT:
            st.commit_latencies.append(r.latency_s)
            forwarded.update(i for i, seg in enumerate(conv.segments) if _covered(seg, r))
            leaked.update(i for i, seg in enumerate(conv.segments) if _leaks(seg, r))
    st.segments += len(conv.segments)
    for i, (a, b, spk) in enumerate(conv.segments):
        if spk == owner:
            cell = st.owner.setdefault(_bucket(b - a), [0, 0])
            cell[0] += i not in forwarded
            cell[1] += 1
        else:
            st.non_owner[0] += i in leaked
            st.non_owner[1] += 1


def score_collab(st: Stats, conv: Conversation, records, provider, stranger: str) -> None:
    """A segment is correct if the turn overlapping it most was committed with its true label."""
    committed = [r for r in records if r.verdict is Verdict.ACCEPT]
    assert len(committed) == len(provider.turns)
    label_of = {id(r): t.speaker_label for r, t in zip(committed, provider.turns, strict=True)}
    got: dict[int, str] = {}  # segment -> label of the committed turn overlapping it most
    for i, seg in enumerate(conv.segments):
        best = max(committed, key=lambda r: _overlap(seg, r), default=None)
        if best is not None and _covered(seg, best):
            got[i] = label_of[id(best)]
    for i, (_, _, spk) in enumerate(conv.segments):
        want = UNKNOWN_LABEL if spk == stranger else spk
        ok = got.get(i) == want
        for cell in (st.attrib, st.attrib_unknown if spk == stranger else st.attrib_enrolled):
            cell[0] += ok
            cell[1] += 1


def pct(a: float, b: float) -> str:
    return "n/a" if not b else f"{100 * a / b:.1f}% ({int(a)}/{int(b)})"


def ms(x: list[float], q: float) -> str:
    return "n/a" if not x else f"{1000 * float(np.percentile(x, q)):.0f} ms"


def render(emb_name: str, st: Stats, header: dict, wall_s: float) -> str:
    n_c = len(st.commit_latencies)
    if n_c < MIN_LATENCY_N:
        verdict = f"INSUFFICIENT DATA (n={n_c} < {MIN_LATENCY_N} commits)"
    else:
        p95 = float(np.percentile(st.commit_latencies, 95))
        verdict = "MEETS" if p95 <= TARGET_S else "MISSES"
    L = [f"# Pipeline benchmark: {emb_name}", ""]
    L += [f"- {k}: {v}" for k, v in header.items()]
    L += [f"- wall time: {wall_s:.0f}s", ""]
    L += ["## Latency: end of speech -> commit (isolation pipeline, realtime pacing)", ""]
    L += [
        f"- {st.segments} ground-truth segments -> {st.records} pipeline turns "
        f"({len(st.commit_latencies)} committed; read speech pauses split some segments), "
        f"{st.early} decided early, {st.errors} dropped by errors",
        f"- **commit latency (n={n_c}): p50 {ms(st.commit_latencies, 50)}, "
        f"p95 {ms(st.commit_latencies, 95)}** vs {1000 * TARGET_S:.0f} ms target: {verdict}",
        f"- decision latency, commit or drop (n={len(st.latencies)}; drops skip continuity "
        f"and send, so this is not the commit target): p50 {ms(st.latencies, 50)}, "
        f"p95 {ms(st.latencies, 95)}",
        f"- of which endpointing (segmenter hangover, stream time): p50 {ms(st.endpoint, 50)}, "
        f"p95 {ms(st.endpoint, 95)}; compute after TurnEnd (wall): p50 {ms(st.compute, 50)}, "
        f"p95 {ms(st.compute, 95)}",
        "",
        "## Isolation (owner only reaches the model)",
        "",
        "Owner false rejection (FR) by segment length; a segment is rejected if no committed "
        "turn covers >= 0.3 s (or half) of it.",
        "",
        "| segment length | FR |",
        "|---|---|",
    ]
    tot = [0, 0]
    for i, (lo, hi) in enumerate(BUCKETS):
        r, n = st.owner.get(i, [0, 0])
        tot[0] += r
        tot[1] += n
        name = (
            f"< {hi:g} s"
            if lo == 0
            else (f">= {lo:g} s" if hi == float("inf") else f"{lo:g}-{hi:g} s")
        )
        L.append(f"| {name} | {pct(r, n)} |")
    L += [f"| all | {pct(*tot)} |", ""]
    L += [
        f"Non-owner false acceptance (FA; enrolled others + stranger): "
        f"**{pct(*st.non_owner)}** of segments with any audio in a committed turn "
        "(pre-roll/tail included).",
        "",
        "## Collaboration attribution (3 enrolled + stranger labelled Unknown)",
        "",
        f"- overall: **{pct(*st.attrib)}** of segments committed with the right label",
        f"- enrolled speakers: {pct(*st.attrib_enrolled)}; stranger -> Unknown: "
        f"{pct(*st.attrib_unknown)}",
        "",
        f"Notes: min decision length {MIN_DECISION_S:g} s, threshold {_threshold(emb_name):g}"
        f"{' (AS-norm cohort)' if COHORT else ''}, "
        "identification on a thread, MockProvider (no network). Read, clean speech, no added "
        "noise; live audio will be harder (see T-023).",
        "",
    ]
    return "\n".join(L)


def build(
    corpus,
    emb,
    n_conv: int,
    n_turns: int,
    seed: int,
    speed: float,
    window_drop=None,
    pause=(0.4, 1.0),
):
    """Stream n_conv conversations twice (isolation, collaboration); return Stats."""
    st = Stats()
    all_spk = [s for s in corpus.speakers() if s not in EXCLUDE]
    rng = np.random.default_rng([seed, 18])
    for c in range(n_conv):
        spks = [all_spk[i] for i in rng.choice(len(all_spk), N_ENROLLED + 1, replace=False)]
        enrolled, stranger = spks[:N_ENROLLED], spks[N_ENROLLED]
        sigs = list(enroll_speakers(corpus, enrolled, emb, N_ENROLL_UTTS, 0).values())
        conv = make_conversation(
            corpus, spks, n_turns, pause_range=pause, seed=seed * 1000 + c, n_enroll=N_ENROLL_UTTS
        )
        owner = enrolled[0]
        pol = IsolationPolicy(
            owner,
            _threshold(emb.name),
            window_drop=window_drop,
            embedder=emb.name,
            score_scale=COHORT.score_scale if COHORT else 1.0,
        )
        recs, _ = stream(conv, sigs, emb, pol, speed)
        score_isolation(st, conv, recs, owner)
        pol = CollaborationPolicy(
            enrolled, "label", _threshold(emb.name), window_drop=window_drop, embedder=emb.name
        )  # score_scale is bound from the matcher
        recs, prov = stream(conv, sigs, emb, pol, speed)
        score_collab(st, conv, recs, prov, stranger)
        print(f"conversation {c + 1}/{n_conv} done", file=sys.stderr)
    return st


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="voiceroom.bench.pipeline")
    p.add_argument("--data", default="data/LibriSpeech")
    p.add_argument("--subset", default="dev-clean")
    p.add_argument("--embedder", default="wespeaker-resnet34")
    p.add_argument("--out", default="reports")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--conversations", type=int, default=4)
    p.add_argument("--turns", type=int, default=6)
    p.add_argument("--speed", type=float, default=1.0, help="pacing factor; 1 = realtime")
    p.add_argument("--window-drop", type=float, default=None, help="default: config value")
    p.add_argument("--cohort", action="store_true", help="AS-norm with data/cohort-<embedder>.npz")
    a = p.parse_args(argv)
    global COHORT, EXCLUDE
    shipped = load_default(a.embedder, load_key())
    if a.cohort and shipped is None:
        print(
            "bench.pipeline: no cohort file; run python -m voiceroom.bench.cohort", file=sys.stderr
        )
        return 2
    COHORT = shipped if a.cohort else None
    EXCLUDE = frozenset(shipped.speakers) if shipped else frozenset()
    root = Path(a.data)
    if not (root / a.subset).is_dir() or not DEFAULT_MODEL.exists():
        print(
            "bench.pipeline: need data/LibriSpeech and models/silero_vad.onnx "
            "(scripts/fetch_data.py, scripts/fetch_models.py)",
            file=sys.stderr,
        )
        return 2
    emb = get_embedder(a.embedder)
    corpus = LibriSpeechCorpus(root, a.subset)
    emb.embed(np.zeros(16000, np.float32))  # warm-up
    t0 = time.monotonic()
    st = build(corpus, emb, a.conversations, a.turns, a.seed, a.speed, a.window_drop)
    header = {
        "dataset": f"LibriSpeech {a.subset}",
        "config": f"conversations={a.conversations} turns={a.turns} seed={a.seed} "
        f"speed={a.speed:g}x window_drop={a.window_drop or window_drop_for(emb.name)} "
        f"window_bad_run={window_bad_run_for(emb.name)} "
        f"enroll_utts={N_ENROLL_UTTS} VAD=SileroVAD",
    }
    out = Path(a.out) / f"pipeline-{emb.name}{'-asnorm' if COHORT else ''}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(emb.name, st, header, time.monotonic() - t0))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
