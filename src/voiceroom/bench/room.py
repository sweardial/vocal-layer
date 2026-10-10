"""Room benchmark: multi-track LibriSpeech conversations through RoomPipeline.

    python -m voiceroom.bench.room [--embedder NAME] [--conversations N] [--out reports]

Each conversation has 3 participants with their own mics (verified tracks), 2 more enrolled
members on one shared mic, and a stranger. Turns are sequential. Some are impostor turns (a
stranger or another member speaking on a participant's track). With bleed, every speaker also
leaks onto every other track at -12 dB (delayed, slightly low-passed). Variants: clean, bleed,
and bleed + narrowband (300-3400 Hz, D-017). Streams at realtime pacing with SileroVAD, the real
embedder and MockProvider. Writes reports/room-<embedder>.md, incl. a verify-threshold sweep.
"""

import argparse
import asyncio
import functools
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

from voiceroom.adapters import MockProvider
from voiceroom.audio import SAMPLE_RATE
from voiceroom.bench.pipeline import (
    BUCKETS,
    MIN_LATENCY_N,
    N_ENROLL_UTTS,
    TARGET_S,
    _bucket,
    _covered,
    _leaks,
    ms,
    paced_frames,
    pct,
)
from voiceroom.bench.run import enroll_speakers
from voiceroom.bench.windows import NarrowbandCorpus
from voiceroom.config import TRACK_LOW_RUN, VERIFY_THRESHOLD_RELAX, threshold_for
from voiceroom.embedders import get_embedder
from voiceroom.eval.conversations import split_utterances
from voiceroom.eval.corpus import LibriSpeechCorpus
from voiceroom.matcher import Matcher
from voiceroom.policy import TrackPolicy, Verdict
from voiceroom.room import RoomPipeline
from voiceroom.vad import DEFAULT_MODEL, FRAME_SIZE

BLEED_DB = -12.0
NOISE_DB = -60.0  # noise floor on every track
SPEECH_RMS = 0.05  # every utterance is normalised to this before mixing
MIC = "mic"  # the shared-mic track
OWN = ("p1", "p2", "p3")  # participants with their own mic
SHARED = ("s1", "s2")  # members on the shared mic
LENGTHS = (0.7, 1.3, 1.8, 2.5, 3.5, 5.0)  # crop targets (s), so every length bucket gets data
P_IMPOSTOR, P_SHARED, P_TAIL = 0.2, 0.2, 0.25
TAIL_LENGTHS = (1.3, 1.8, 2.5)  # owner part of a spliced turn (s)
P_TAIL_STRANGER = 0.75  # share of tails spoken by the stranger (rest: another member)
STRANGER_TAIL_LENGTHS = (0.6, 0.8, 1.0, 1.3, 1.8, 2.5)  # incl. tails around the LOW_RUN limit
TAIL_GAP_S = 0.15  # pause between owner and tail: inside one VAD turn
RELAXES = (0.0, 0.025, 0.05, 0.075, 0.1, 0.125, 0.15, 0.2)  # verify threshold = threshold - relax
VARIANTS = (("clean", False, False), ("bleed", True, False), ("bleed+narrowband", True, True))


@dataclass
class Seg:
    t0: float
    t1: float
    speaker: str
    track: str  # the track the speech is loud on
    kind: str  # own | shared | imp_member | imp_stranger | tail_member | tail_stranger
    audio: np.ndarray = field(repr=False, default=None)  # type: ignore[assignment]
    tail_t0: float = 0.0  # tail_*: where the owner stops and the other voice starts


@dataclass
class RoomConv:
    tracks: dict[str, np.ndarray]
    segs: list[Seg]
    bleed: bool


def _crop(x: np.ndarray, rng: np.random.Generator, lengths=None) -> np.ndarray:
    n = int(rng.choice(LENGTHS if lengths is None else lengths) * SAMPLE_RATE)
    if len(x) > n:
        i = int(rng.integers(0, len(x) - n + 1))
        x = x[i : i + n]
    x = x.astype(np.float32)
    return x * np.float32(SPEECH_RMS / (float(np.sqrt(np.mean(x**2))) + 1e-9))


def make_room_conversation(
    test: dict[str, list],
    own: dict[str, str],
    shared: list[str],
    stranger: str,
    n_turns: int,
    bleed: bool,
    seed: int,
    pause=(0.4, 1.0),
) -> RoomConv:
    """`own`: track -> its participant. Turns are sequential; speech lands loud on one track."""
    rng = np.random.default_rng([seed, 42])
    members = [*own.values(), *shared]
    segs, pos, prev = [], int(0.5 * SAMPLE_RATE), None
    for _ in range(n_turns):
        r = rng.random()
        if r < P_IMPOSTOR:
            track = str(rng.choice(list(own)))
            pool = [m for m in members if m != own[track]] + [stranger]
            spk = str(rng.choice(pool))
            kind = "imp_stranger" if spk == stranger else "imp_member"
        elif r < P_IMPOSTOR + P_SHARED:
            track, spk, kind = MIC, str(rng.choice(shared)), "shared"
        elif r < P_IMPOSTOR + P_SHARED + P_TAIL:
            # the owner speaks, then someone else (stranger / other member, e.g. revoked) carries on
            track = str(rng.choice(list(own)))
            spk = own[track]
            pool = [m for m in members if m != spk]
            other = stranger if rng.random() < P_TAIL_STRANGER else str(rng.choice(pool))
            if spk == prev:
                continue
            xo = _crop(test[spk][int(rng.integers(len(test[spk])))].load(), rng, TAIL_LENGTHS)
            xt = _crop(
                test[other][int(rng.integers(len(test[other])))].load(), rng, STRANGER_TAIL_LENGTHS
            )
            gap = np.zeros(int(TAIL_GAP_S * SAMPLE_RATE), np.float32)
            x = np.concatenate([xo, gap, xt])
            split = (pos + len(xo) + len(gap)) / SAMPLE_RATE
            kind = "tail_stranger" if other == stranger else "tail_member"
            segs.append(
                Seg(pos / SAMPLE_RATE, (pos + len(x)) / SAMPLE_RATE, spk, track, kind, x, split)
            )
            pos += len(x) + int(rng.uniform(*pause) * SAMPLE_RATE)
            prev = other
            continue
        else:
            track = str(rng.choice(list(own)))
            spk, kind = own[track], "own"
        if spk == prev:  # never the same speaker twice in a row
            continue
        utt = test[spk][int(rng.integers(len(test[spk])))]
        x = _crop(utt.load(), rng)
        segs.append(Seg(pos / SAMPLE_RATE, (pos + len(x)) / SAMPLE_RATE, spk, track, kind, x))
        pos += len(x) + int(rng.uniform(*pause) * SAMPLE_RATE)
        prev = spk
    n = (pos + SAMPLE_RATE) // FRAME_SIZE * FRAME_SIZE + FRAME_SIZE
    floor = 10 ** (NOISE_DB / 20)
    tracks = {t: (floor * rng.standard_normal(n)).astype(np.float32) for t in (*own, MIC)}
    gain = np.float32(10 ** (BLEED_DB / 20))
    for s in segs:
        a = int(round(s.t0 * SAMPLE_RATE))
        tracks[s.track][a : a + len(s.audio)] += s.audio
        if bleed:
            copy = lfilter([0.5, 0.5], [1.0], s.audio).astype(np.float32) * gain
            for t in tracks:
                if t != s.track:
                    d = a + int(rng.integers(40, 240))  # 2.5-15 ms of room delay
                    tracks[t][d : d + len(copy)] += copy[: max(0, n - d)]
    return RoomConv(tracks, segs, bleed)


def _policy_factory(emb_name, members, verify):
    def make(sid: str, claimed: str | None):
        if claimed is None:
            return TrackPolicy("shared", members, None, "label", embedder=emb_name)
        return TrackPolicy(
            "verified", members, claimed, "drop", embedder=emb_name, verify_threshold=verify
        )

    return make


def stream(conv, sigs, claims, members, emb, verify, speed):
    provider = MockProvider()
    room = RoomPipeline(
        provider,
        _policy_factory(emb.name, members, verify),
        Matcher(sigs, threshold=threshold_for(emb.name), embedder=emb),
        max_pending=1000,
        isolation="thread",
    )
    for tid, audio in conv.tracks.items():
        room.add_source(tid, paced_frames(audio, speed), claims.get(tid), t_offset=0.0)
    asyncio.run(room.run(stop_when_idle=True))
    return list(room.records), provider


@dataclass
class Stats:
    owner: dict[int, list[int]] = field(default_factory=dict)  # bucket -> [rejected, total]
    imp: dict[str, list[int]] = field(default_factory=dict)  # kind -> [accepted, total]
    dup: dict[str, list[int]] = field(default_factory=dict)  # target mode -> [duplicated, copies]
    attrib: dict[str, list[int]] = field(default_factory=dict)  # own | shared -> [correct, total]
    latencies: list[float] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)
    errors: int = 0
    records: int = 0
    # verify sweep: (length, scores of claimed, other-member-confident?) per owner / impostor seg
    sweep_owner: list[tuple[float, float, bool, str]] = field(default_factory=list)
    sweep_imp: list[tuple[float, float, bool, str]] = field(default_factory=list)


def _cell(d: dict, key, ok: bool) -> None:
    c = d.setdefault(key, [0, 0])
    c[0] += ok
    c[1] += 1


def score(st: Stats, conv: RoomConv, recs, provider, claims: dict) -> None:
    committed = [r for r in recs if r.verdict is Verdict.ACCEPT]
    assert len(committed) == len(provider.turns)
    label_of = {id(r): t.speaker_label for r, t in zip(committed, provider.turns, strict=True)}
    st.records += len(recs)
    st.errors += sum(r.error is not None for r in recs)
    for r in committed:
        st.latencies.append(r.latency_s)
        st.waits.append(r.queue_wait_s)
    for s in conv.segs:
        on = [r for r in committed if r.source_id == s.track]
        if s.kind in ("own", "shared"):
            cov = [r for r in on if _covered((s.t0, s.t1), r)]
            if s.kind == "own":
                _cell(st.owner, _bucket(s.t1 - s.t0), not cov)
            ok = any(label_of[id(r)] == s.speaker for r in cov)
            _cell(st.attrib, s.kind, ok)
            if conv.bleed:
                for tid in conv.tracks:
                    if tid != s.track:
                        copies = [r for r in committed if r.source_id == tid]
                        dup = any(_covered((s.t0, s.t1), r) for r in copies)
                        _cell(st.dup, "verified" if claims[tid] else "shared", dup)
        elif s.kind.startswith("tail"):
            key = f"{s.kind} {'<1.1' if s.t1 - s.tail_t0 < 1.1 else '>=1.1'} s"
            _cell(st.imp, key, any(_leaks((s.tail_t0, s.t1), r) for r in on))
        else:
            _cell(st.imp, s.kind, any(_leaks((s.t0, s.t1), r) for r in on))


def sweep_scores(st: Stats, conv: RoomConv, matcher: Matcher, emb, claims: dict) -> None:
    """Whole-turn 1:1 score of the claimed member for owner and impostor segments (no VAD)."""
    for s in conv.segs:
        if s.kind not in ("own", "imp_member", "imp_stranger"):
            continue
        m = matcher.identify_embedding(emb.embed(s.audio))
        claimed = claims[s.track]
        other = m.name is not None and m.name != claimed
        row = (s.t1 - s.t0, m.scores.get(claimed, float("-inf")), other, s.kind)
        (st.sweep_owner if s.kind == "own" else st.sweep_imp).append(row)


def make_work(corpus, emb, n_conv, n_turns, seed, bleed, n_enroll=N_ENROLL_UTTS):
    """`n_conv` conversations as (conv, signatures, claims, enrolled members)."""
    all_spk = corpus.speakers()
    rng = np.random.default_rng([seed, 19])
    sig_cache, work = {}, []
    names = [*OWN, *SHARED]
    for c in range(n_conv):
        spks = [all_spk[i] for i in rng.choice(len(all_spk), len(names) + 1, replace=False)]
        enrolled, stranger = spks[:-1], spks[-1]
        sig_cache.update(
            enroll_speakers(corpus, [s for s in enrolled if s not in sig_cache], emb, n_enroll, 0)
        )
        sigs = [sig_cache[s] for s in enrolled]
        _, test = split_utterances(corpus, [*enrolled, stranger], n_enroll, 0)
        own = dict(zip(OWN, enrolled[: len(OWN)], strict=True))
        conv = make_room_conversation(
            test, own, enrolled[len(OWN) :], stranger, n_turns, bleed, seed * 1000 + c
        )
        claims = {**own, MIC: None}
        work.append((conv, sigs, claims, enrolled))
    return work


def build(
    corpus, emb, n_conv, n_turns, seed, speed, bleed, verify, jobs, n_enroll=N_ENROLL_UTTS,
    n_stream=None, sweep=False,
):  # fmt: skip
    """Generate `n_conv` conversations; stream the first `n_stream`; score sweeps if `sweep`."""
    st = Stats()
    n_stream = n_conv if n_stream is None else n_stream
    work = make_work(corpus, emb, n_conv, n_turns, seed, bleed, n_enroll)
    matchers = [Matcher(w[1], threshold=threshold_for(emb.name), embedder=emb) for w in work]
    for (conv, _, claims, _), mt in zip(work, matchers, strict=True):
        if sweep:
            sweep_scores(st, conv, mt, emb, claims)
    work = work[:n_stream]
    with ThreadPoolExecutor(max(jobs, 1)) as pool:
        futs = [pool.submit(stream, cv, sg, cl, en, emb, verify, speed) for cv, sg, cl, en in work]
        try:
            for c, ((conv, _, claims, _), f) in enumerate(zip(work, futs, strict=True)):
                recs, prov = f.result()
                score(st, conv, recs, prov, claims)
                print(f"  conversation {c + 1}/{n_stream} done", file=sys.stderr)
        except BaseException:
            pool.shutdown(cancel_futures=True)
            raise
    return st


def merge(a: Stats, b: Stats) -> Stats:
    out = Stats()
    for name in ("owner", "imp", "dup", "attrib"):
        for src in (a, b):
            for k, (x, n) in getattr(src, name).items():
                c = getattr(out, name).setdefault(k, [0, 0])
                c[0] += x
                c[1] += n
    for f in ("latencies", "waits", "sweep_owner", "sweep_imp"):
        getattr(out, f).extend(getattr(a, f) + getattr(b, f))
    out.errors, out.records = a.errors + b.errors, a.records + b.records
    return out


def _tail(st: Stats, kind: str) -> list[int]:
    v = [c for k, c in st.imp.items() if k.startswith(kind)]
    return [sum(c[0] for c in v), sum(c[1] for c in v)]


def render(emb_name, runs: dict[str, Stats], header: dict, wall_s: float, verify: float) -> str:
    L = [f"# Room benchmark: {emb_name}", ""]
    L += [f"- {k}: {v}" for k, v in header.items()] + [f"- wall time: {wall_s:.0f}s", ""]
    thr = threshold_for(emb_name)
    L += ["## Summary (verified tracks use the config verify threshold)", ""]
    L += [
        "| variant | owner FR >= 1 s | impostor accept | stranger-tail accept | member-tail accept "
        "| bleed dup (verified / shared track) "
        "| attribution own / shared | commit p50 | commit p95 | queue wait p95 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, st in runs.items():
        ge1 = [
            sum(v[0] for k, v in st.owner.items() if k >= 1),
            sum(v[1] for k, v in st.owner.items() if k >= 1),
        ]
        imp = [
            sum(v[0] for k, v in st.imp.items() if not k.startswith("tail")),
            sum(v[1] for k, v in st.imp.items() if not k.startswith("tail")),
        ]

        d = st.dup
        dup = f"{pct(*d.get('verified', [0, 0]))} / {pct(*d.get('shared', [0, 0]))}"
        L.append(
            f"| {name} | {pct(*ge1)} | {pct(*imp)} | {pct(*_tail(st, 'tail_stranger'))} | "
            f"{pct(*_tail(st, 'tail_member'))} | {dup} | "
            f"{pct(*st.attrib.get('own', [0, 0]))} / {pct(*st.attrib.get('shared', [0, 0]))} | "
            f"{ms(st.latencies, 50)} | {ms(st.latencies, 95)} | {ms(st.waits, 95)} |"
        )
    allst = functools.reduce(merge, runs.values())
    n_c = len(allst.latencies)
    if n_c < MIN_LATENCY_N:
        verdict = f"INSUFFICIENT DATA (n={n_c} < {MIN_LATENCY_N} commits)"
    else:
        p95 = float(np.percentile(allst.latencies, 95))
        verdict = "MEETS" if p95 <= TARGET_S else "MISSES"
    L += [
        "",
        f"Commit latency = end of speech -> send_turn done, queue wait included "
        f"(all variants, n={n_c}): p50 {ms(allst.latencies, 50)}, p95 {ms(allst.latencies, 95)} "
        f"vs {1000 * TARGET_S:.0f} ms target: **{verdict}**. {allst.errors} of "
        f"{allst.records} pipeline records were errors.",
        "",
    ]
    for name, st in runs.items():
        L += [f"## {name}", "", "Owner false rejection on own track, by segment length:", ""]
        L += ["| segment length | FR |", "|---|---|"]
        tot = [0, 0]
        for i, (lo, hi) in enumerate(BUCKETS):
            r, n = st.owner.get(i, [0, 0])
            tot[0] += r
            tot[1] += n
            label = (
                f"< {hi:g} s"
                if lo == 0
                else (f">= {lo:g} s" if hi == float("inf") else f"{lo:g}-{hi:g} s")
            )
            L.append(f"| {label} | {pct(r, n)} |")
        L += [f"| all | {pct(*tot)} |", ""]
        L += [
            "Impostor on a member's track, accepted (any audio in a committed turn; tail_*: other "
            "voice spliced after the owner in one turn, same rule on the tail part): "
            + ", ".join(f"{k} {pct(*v)}" for k, v in sorted(st.imp.items())),
            "",
        ]
        if st.dup:
            L += [
                "Bleed duplicates: a bleed copy of someone's turn (-12 dB) on another track "
                "that still reached the model as a turn of its own. "
                + ", ".join(f"{k} track {pct(*v)}" for k, v in sorted(st.dup.items())),
                "",
            ]
    L += [
        "## Verify threshold sweep",
        "",
        f"Whole-turn 1:1 score of the claimed member, no VAD, no continuity check; identification "
        f"threshold {thr:g}; a turn that confidently matches another member is rejected. "
        f"Config verify threshold: {verify:.3f}. Sequential conversations: the loud audio on a "
        "track is bleed-free, so bleed does not change these scores.",
        "",
    ]
    L += sweep_rows(runs, thr)
    L.append("")
    return "\n".join(L)


def sweep_rows(runs: dict[str, Stats], thr: float) -> list[str]:
    L = ["| variant | verify thr | owner FR >= 1 s | impostor accept | member | stranger |"]
    L += ["|---|---|---|---|---|---|"]
    for name, st in runs.items():
        if not st.sweep_owner:
            continue
        own = [(sc, oth) for n, sc, oth, _ in st.sweep_owner if n >= 1.0]
        for relax in RELAXES:
            v = thr - relax
            rej = sum(not (sc >= v and not oth) for sc, oth in own)
            cells = []
            for kinds in (("imp_member", "imp_stranger"), ("imp_member",), ("imp_stranger",)):
                acc = [sc >= v and not oth for _, sc, oth, k in st.sweep_imp if k in kinds]
                cells.append(pct(sum(acc), len(acc)))
            L.append(
                f"| {name} | {v:.3f} (-{relax:g}) | {pct(rej, len(own))} | "
                + " | ".join(cells)
                + " |"
            )
    return L


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="voiceroom.bench.room")
    p.add_argument("--data", default="data/LibriSpeech")
    p.add_argument("--subset", default="dev-clean")
    p.add_argument("--embedder", default="wespeaker-resnet34")
    p.add_argument("--out", default="reports")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument(
        "--conversations", type=int, default=12, help="per variant (livekit: total, at least 5)"
    )
    p.add_argument("--sweep-conversations", type=int, default=12, help="verify sweep (no stream)")
    p.add_argument("--turns", type=int, default=16)
    p.add_argument("--speed", type=float, default=1.0, help="pacing factor; 1 = realtime")
    p.add_argument("--jobs", type=int, default=4, help="conversations streamed concurrently")
    p.add_argument("--verify", type=float, default=None, help="default: config value")
    p.add_argument(
        "--transport",
        choices=("fake", "livekit"),
        default="fake",
        help="livekit: only measure latency through a local livekit-server (T-049) and write "
        "its section into the existing report",
    )
    a = p.parse_args(argv)
    root = Path(a.data)
    if not (root / a.subset).is_dir() or not DEFAULT_MODEL.exists():
        print("bench.room: need data/LibriSpeech and models/silero_vad.onnx", file=sys.stderr)
        return 2
    emb = get_embedder(a.embedder)
    base = LibriSpeechCorpus(root, a.subset)
    emb.embed(np.zeros(16000, np.float32))
    if a.transport == "livekit":
        from voiceroom.bench import room_livekit

        probe = TrackPolicy("shared", ["x"], embedder=emb.name)
        verify = a.verify if a.verify is not None else probe.threshold - VERIFY_THRESHOLD_RELAX
        return room_livekit.bench(base, emb, a, verify, Path(a.out))
    t0 = time.monotonic()
    runs = {}
    for name, bleed, nb in VARIANTS:
        print(f"variant {name}", file=sys.stderr)
        corpus = NarrowbandCorpus(base) if nb else base
        sweep = name != "bleed"  # identical audio to "clean" on the loud track
        runs[name] = build(
            corpus,
            emb,
            max(a.conversations, a.sweep_conversations) if sweep else a.conversations,
            a.turns,
            a.seed,
            a.speed,
            bleed,
            a.verify,
            a.jobs,
            N_ENROLL_UTTS + 4 * nb,
            n_stream=a.conversations,
            sweep=sweep,
        )
    probe = TrackPolicy("shared", ["x"], embedder=emb.name)
    verify = a.verify if a.verify is not None else probe.threshold - VERIFY_THRESHOLD_RELAX
    header = {
        "dataset": f"LibriSpeech {a.subset}",
        "config": f"conversations={a.conversations}/variant turns={a.turns} seed={a.seed} "
        f"track_low_run={TRACK_LOW_RUN} p_tail={P_TAIL} speed={a.speed:g}x jobs={a.jobs} "
        f"tracks=3 own + 1 shared (2 members) "
        f"sweep_conversations={a.sweep_conversations} enroll_utts={N_ENROLL_UTTS}"
        "(+4 narrowband) VAD=SileroVAD MockProvider",
    }
    out = Path(a.out) / f"room-{emb.name}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(emb.name, runs, header, time.monotonic() - t0, verify))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
