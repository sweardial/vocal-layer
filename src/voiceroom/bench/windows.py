"""Calibrate the speaker-continuity window check (`IsolationPolicy.windows_ok`) on bench audio.

    python -m voiceroom.bench.windows [--embedder NAME] [--trials N] [--out reports]

Builds owner turns (>= 1 s of back-to-back owner utterances) and the same turns with an interior
span replaced by another speaker (an interjection with 1 s of owner speech on each side), scores
the pipeline's ~0.5 s windows with a real embedder, and sweeps `window_drop`. Reports owner
false-reject (turns >= 1 s) against interior-interjection leak rate. Writes
reports/windows-<embedder>.md. Like the pipeline, SileroVAD marks pauses: windows under
`MIN_VOICED` speech are unscored (break a run) and scored windows embed speech frames only.
`--no-vad` counts every frame as voiced (the pre-T-032 bench).
"""

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
from scipy import signal

from voiceroom.audio import SAMPLE_RATE
from voiceroom.bench.run import enroll_speakers
from voiceroom.config import threshold_for
from voiceroom.embedders import get_embedder
from voiceroom.eval.conversations import split_utterances
from voiceroom.eval.corpus import LibriSpeechCorpus
from voiceroom.matcher import Matcher
from voiceroom.pipeline import HOP_FRAMES, MIN_VOICED, WIN_FRAMES, _window, is_short_turn
from voiceroom.policy import IsolationPolicy
from voiceroom.vad import DEFAULT_MODEL, FRAME_SIZE, SileroVAD

DROPS = (0.02, 0.03, 0.05, 0.08, 0.1, 0.12, 0.15, 0.2, 0.3, 0.4, 1.0)
BUFFERS = [0.1, 0.2, 0.3, 0.4, 10.0]  # 10: no absolute floor (best-candidate check only)
INTERJECT_S = (0.5, 1.0, 2.0)
SHORT_TAIL_S = (0.5, 0.75)  # tail after ~1 s of owner speech (turn < CONTINUITY_MIN_S)
EDGE_S = 1.0  # owner speech kept on each side of an interjection
TURN_S = (3.0, 6.0)  # turn length range (cropped from a single utterance)
N_ENROLL_UTTS = 5


class NarrowbandCorpus:
    """Corpus view with every utterance band-limited to 300-3400 Hz (phone-like), enrollment too."""

    def __init__(self, inner):
        self.inner = inner
        self._sos = signal.butter(6, [300, 3400], "bandpass", fs=SAMPLE_RATE, output="sos")

    def speakers(self) -> list[str]:
        return self.inner.speakers()

    def utterances(self, speaker: str) -> list:
        return [
            replace(u, _load=lambda u=u: self._filter(u.load()))
            for u in self.inner.utterances(speaker)
        ]

    def _filter(self, a: np.ndarray) -> np.ndarray:
        return signal.sosfilt(self._sos, a).astype(np.float32)


def _window_starts(n: int, win: int, hop: int) -> list[int]:
    starts = list(range(0, n - win + 1, hop))
    if n - win not in starts:
        starts.append(n - win)
    return starts


_VAD: SileroVAD | None = None  # set by main(); None: every frame counts as voiced


def voiced_mask(audio: np.ndarray) -> np.ndarray:
    n = len(audio) // FRAME_SIZE
    if _VAD is None:
        return np.ones(n, bool)
    _VAD.reset()
    frames = audio[: n * FRAME_SIZE].reshape(n, FRAME_SIZE)
    return np.array([_VAD.process(f) >= 0.5 for f in frames], bool)


def window_matches(audio: np.ndarray, matcher: Matcher, win: int, hop: int) -> list:
    """Matches for the windows the pipeline scores over a turn (same starts and width);
    None for a pause window."""
    n = len(audio) // FRAME_SIZE
    voiced = voiced_mask(audio)
    return [
        matcher.identify(_window(audio, voiced, s, win))
        if voiced[s : s + win].mean() >= MIN_VOICED
        else None
        for s in _window_starts(n, win, hop)
    ]


def _scored(audio, matcher, win, hop):
    """(matches, short) as the pipeline would pick the continuity rule for this turn."""
    return window_matches(audio, matcher, win, hop), is_short_turn(len(audio) // FRAME_SIZE)


def _speech(pool, rng, seconds: float) -> np.ndarray:
    """`seconds` cropped from one utterance of `pool` (one recording, as a real turn is)."""
    n = int(seconds * SAMPLE_RATE)
    long = [u for u in pool if u.duration >= seconds + 0.1]
    if not long:
        raise ValueError(f"no utterance >= {seconds:.1f} s")
    a = long[rng.integers(len(long))].load().astype(np.float32)
    start = rng.integers(0, len(a) - n + 1)
    return a[start : start + n].copy()


def collect(
    corpus, emb, n_trials: int, seed: int, win: int, hop: int, turn_s=TURN_S, n_enroll=N_ENROLL_UTTS
) -> dict:
    """Window matches per case: owner turns and interjection turns by length / interloper kind."""
    rng = np.random.default_rng([seed, 28])
    spks = corpus.speakers()
    res = {"owner": []} | {(j, k): [] for j in INTERJECT_S for k in ("enrolled", "stranger")}
    res |= {("short", j, k): [] for j in SHORT_TAIL_S for k in ("enrolled", "stranger")}
    for t in range(n_trials):
        pick = [spks[i] for i in rng.choice(len(spks), 4, replace=False)]
        enrolled, stranger = pick[:3], pick[3]
        sigs = list(enroll_speakers(corpus, enrolled, emb, n_enroll, 0).values())
        matcher = Matcher(sigs, threshold=threshold_for(emb.name), embedder=emb)
        _, rest = split_utterances(corpus, pick, n_enroll, 0)
        for _ in range(2):
            owner_turn = _speech(rest[enrolled[0]], rng, rng.uniform(*turn_s))
            res["owner"].append((enrolled[0], *_scored(owner_turn, matcher, win, hop)))
        for j in INTERJECT_S:
            for kind, who in (("enrolled", enrolled[1 + t % 2]), ("stranger", stranger)):
                total = EDGE_S * 2 + j + rng.uniform(0, 1.5)
                turn = _speech(rest[enrolled[0]], rng, total)
                a = int((EDGE_S + rng.uniform(0, total - 2 * EDGE_S - j)) * SAMPLE_RATE)
                b = a + int(j * SAMPLE_RATE)
                turn[a:b] = _speech(rest[who], rng, j)
                res[(j, kind)].append((enrolled[0], *_scored(turn, matcher, win, hop)))
        for j in SHORT_TAIL_S:  # owner ~1 s then an interloper tail at the end: a short turn
            for kind, who in (("enrolled", enrolled[1 + t % 2]), ("stranger", stranger)):
                turn = _speech(rest[enrolled[0]], rng, 1.0 + rng.uniform(0, 0.2))
                res[("short", j, kind)].append(
                    (
                        enrolled[0],
                        *_scored(
                            np.concatenate([turn, _speech(rest[who], rng, j)]), matcher, win, hop
                        ),
                    )
                )
        print(f"trial {t + 1}/{n_trials}", file=sys.stderr)
    return res


def render(emb_name: str, res: dict, threshold: float, buffers, header: dict, run: int = 1) -> str:
    pct = lambda a, n: f"{100 * a / n:.1f}% ({a}/{n})" if n else "n/a"  # noqa: E731
    L = [f"# Window-continuity calibration: {emb_name}", ""]
    L += [f"- {k}: {v}" for k, v in header.items()]
    L += [f"- threshold {threshold:g}", ""]
    L += ["`owner FR` = owner turns (>= 1 s, no interjection) that `windows_ok` rejects. "]
    L += ["`leak` = turns with an interior interjection that `windows_ok` still passes. "]
    L += ["A window must also have the owner as best candidate (always applied).", ""]
    for buf in buffers:
        L += [f"## window_buffer {buf:g} (floor >= threshold - buffer)", ""]
        cols = [f"leak {j:g} s" for j in INTERJECT_S] + [
            "leak all",
            "leak enrolled",
            "leak stranger",
        ]
        L += ["| window_drop | owner FR | " + " | ".join(cols) + " |"]
        L += ["|---|---|" + "---|" * len(cols)]
        for d in DROPS:
            ok, n = _passes(res["owner"], threshold, d, buf, run)
            row, tot = [], [0, 0]
            for j in INTERJECT_S:
                cases = res[(j, "enrolled")] + res[(j, "stranger")]
                o, m = _passes(cases, threshold, d, buf, run)
                row.append(pct(o, m))
                tot = [tot[0] + o, tot[1] + m]
            row.append(pct(*tot))
            for kind in ("enrolled", "stranger"):
                cases = [m for j in INTERJECT_S for m in res[(j, kind)]]
                row.append(pct(*_passes(cases, threshold, d, buf, run)))
            L.append(f"| {d:g} | {pct(n - ok, n)} | " + " | ".join(row) + " |")
        L.append("")
    L += ["## Short turns (owner ~1 s + interloper tail, `short` rule; leak = still passes)", ""]
    L += ["| tail | enrolled | stranger |", "|---|---|---|"]
    d = DROPS[-2]
    for j in SHORT_TAIL_S:
        row = [
            pct(*_passes(res[("short", j, k)], threshold, d, buffers[0], run))
            for k in ("enrolled", "stranger")
        ]
        L.append(f"| {j:g} s | " + " | ".join(row) + " |")
    L += ["", "Read, clean LibriSpeech; interjections are spliced (no overlap).", ""]
    return "\n".join(L)


def _passes(
    cases: list, threshold: float, drop: float, buffer: float, run: int = 1
) -> tuple[int, int]:
    ok = 0
    for owner, matches, short in cases:
        pol = IsolationPolicy(
            owner, threshold, window_buffer=buffer, window_drop=drop, window_bad_run=run
        )
        ok += pol.windows_ok(matches, short=short)
    return ok, len(cases)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="voiceroom.bench.windows")
    p.add_argument("--data", default="data/LibriSpeech")
    p.add_argument("--subset", default="dev-clean")
    p.add_argument("--embedder", default="wespeaker-resnet34")
    p.add_argument("--out", default="reports")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--trials", type=int, default=30)
    p.add_argument("--bad-run", type=int, default=1, help="consecutive bad windows that reject")
    p.add_argument("--no-vad", action="store_true", help="count every frame as voiced")
    p.add_argument("--buffers", type=float, nargs="+", default=BUFFERS)
    p.add_argument(
        "--win-frames", type=int, default=WIN_FRAMES, help="32 frames = 1 s; hop scales with it"
    )
    p.add_argument(
        "--turn-s", type=float, nargs=2, default=TURN_S, help="owner turn length range (s)"
    )
    p.add_argument("--narrowband", action="store_true", help="band-limit all speech to 300-3400 Hz")
    a = p.parse_args(argv)
    root = Path(a.data)
    if not (root / a.subset).is_dir():
        print("bench.windows: need data/LibriSpeech (scripts/fetch_data.py)", file=sys.stderr)
        return 2
    global _VAD
    _VAD = None if a.no_vad else SileroVAD(DEFAULT_MODEL)
    emb = get_embedder(a.embedder)
    corpus = LibriSpeechCorpus(root, a.subset)
    if a.narrowband:
        corpus = NarrowbandCorpus(corpus)
    res = collect(
        corpus,
        emb,
        a.trials,
        a.seed,
        a.win_frames,
        a.win_frames * HOP_FRAMES // WIN_FRAMES,
        tuple(a.turn_s),
        N_ENROLL_UTTS + 4 * a.narrowband,
    )
    header = {
        "dataset": f"LibriSpeech {a.subset}",
        "config": f"trials={a.trials} seed={a.seed} window={a.win_frames} frames "
        f"hop={a.win_frames * HOP_FRAMES // WIN_FRAMES} bad_run={a.bad_run} vad={not a.no_vad} "
        f"turn_s={tuple(a.turn_s)} narrowband={a.narrowband}",
    }
    out = (
        Path(a.out)
        / f"windows-{emb.name}{'' if a.win_frames == WIN_FRAMES else f'-w{a.win_frames}'}"
        f"{'' if a.bad_run == 1 else f'-run{a.bad_run}'}"
        f"{'-nb' if a.narrowband else ''}{'-short' if tuple(a.turn_s) != TURN_S else ''}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(emb.name, res, threshold_for(emb.name), a.buffers, header, a.bad_run))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
