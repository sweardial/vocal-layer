"""Calibrate the speaker-continuity window check (`IsolationPolicy.windows_ok`) on bench audio.

    python -m voiceroom.bench.windows [--embedder NAME] [--trials N] [--out reports]

Builds owner turns (>= 1 s of back-to-back owner utterances) and the same turns with an interior
span replaced by another speaker (an interjection with 1 s of owner speech on each side), scores
the pipeline's ~0.5 s windows with a real embedder, and sweeps `window_drop`. Reports owner
false-reject (turns >= 1 s) against interior-interjection leak rate. Writes
reports/windows-<embedder>.md. VAD is skipped (all frames count as voiced).
"""

import argparse
import sys
from pathlib import Path

import numpy as np

from voiceroom.audio import SAMPLE_RATE
from voiceroom.bench.run import enroll_speakers
from voiceroom.config import threshold_for
from voiceroom.embedders import get_embedder
from voiceroom.eval.conversations import split_utterances
from voiceroom.eval.corpus import LibriSpeechCorpus
from voiceroom.matcher import Matcher
from voiceroom.pipeline import WIN_FRAMES
from voiceroom.policy import IsolationPolicy
from voiceroom.vad import FRAME_SIZE

DROPS = (0.02, 0.03, 0.05, 0.08, 0.1, 0.12, 0.15, 0.2, 0.3, 1.0)
BUFFERS = [0.1, 0.2, 0.3, 0.4, 10.0]  # 10: no absolute floor (best-candidate check only)
INTERJECT_S = (0.5, 1.0, 2.0)
EDGE_S = 1.0  # owner speech kept on each side of an interjection
TURN_S = (3.0, 6.0)  # turn length range (cropped from a single utterance)
N_ENROLL_UTTS = 5


def _window_starts(n: int, win: int, hop: int) -> list[int]:
    starts = list(range(0, n - win + 1, hop))
    if n - win not in starts:
        starts.append(n - win)
    return starts


def window_matches(audio: np.ndarray, matcher: Matcher, win: int, hop: int) -> list:
    """Matches for the windows the pipeline scores over a turn (same starts and width)."""
    n = len(audio) // FRAME_SIZE
    return [
        matcher.identify(audio[s * FRAME_SIZE : (s + win) * FRAME_SIZE])
        for s in _window_starts(n, win, hop)
    ]


def _speech(pool, rng, seconds: float) -> np.ndarray:
    """`seconds` cropped from one utterance of `pool` (one recording, as a real turn is)."""
    n = int(seconds * SAMPLE_RATE)
    long = [u for u in pool if u.duration >= seconds + 0.1]
    if not long:
        raise ValueError(f"no utterance >= {seconds:.1f} s")
    a = long[rng.integers(len(long))].load().astype(np.float32)
    start = rng.integers(0, len(a) - n + 1)
    return a[start : start + n].copy()


def collect(corpus, emb, n_trials: int, seed: int, win: int, hop: int) -> dict:
    """Window matches per case: owner turns and interjection turns by length / interloper kind."""
    rng = np.random.default_rng([seed, 28])
    spks = corpus.speakers()
    res = {"owner": []} | {(j, k): [] for j in INTERJECT_S for k in ("enrolled", "stranger")}
    for t in range(n_trials):
        pick = [spks[i] for i in rng.choice(len(spks), 4, replace=False)]
        enrolled, stranger = pick[:3], pick[3]
        sigs = list(enroll_speakers(corpus, enrolled, emb, N_ENROLL_UTTS, 0).values())
        matcher = Matcher(sigs, threshold=threshold_for(emb.name), embedder=emb)
        _, rest = split_utterances(corpus, pick, N_ENROLL_UTTS, 0)
        for _ in range(2):
            owner_turn = _speech(rest[enrolled[0]], rng, rng.uniform(*TURN_S))
            res["owner"].append((enrolled[0], window_matches(owner_turn, matcher, win, hop)))
        for j in INTERJECT_S:
            for kind, who in (("enrolled", enrolled[1 + t % 2]), ("stranger", stranger)):
                total = EDGE_S * 2 + j + rng.uniform(0, 1.5)
                turn = _speech(rest[enrolled[0]], rng, total)
                a = int((EDGE_S + rng.uniform(0, total - 2 * EDGE_S - j)) * SAMPLE_RATE)
                b = a + int(j * SAMPLE_RATE)
                turn[a:b] = _speech(rest[who], rng, j)
                res[(j, kind)].append((enrolled[0], window_matches(turn, matcher, win, hop)))
        print(f"trial {t + 1}/{n_trials}", file=sys.stderr)
    return res


def render(emb_name: str, res: dict, threshold: float, buffers, header: dict) -> str:
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
            ok, n = _passes(res["owner"], threshold, d, buf)
            row, tot = [], [0, 0]
            for j in INTERJECT_S:
                cases = res[(j, "enrolled")] + res[(j, "stranger")]
                o, m = _passes(cases, threshold, d, buf)
                row.append(pct(o, m))
                tot = [tot[0] + o, tot[1] + m]
            row.append(pct(*tot))
            for kind in ("enrolled", "stranger"):
                cases = [m for j in INTERJECT_S for m in res[(j, kind)]]
                row.append(pct(*_passes(cases, threshold, d, buf)))
            L.append(f"| {d:g} | {pct(n - ok, n)} | " + " | ".join(row) + " |")
        L.append("")
    L += ["Read, clean LibriSpeech; interjections are spliced (no overlap); VAD skipped.", ""]
    return "\n".join(L)


def _passes(cases: list, threshold: float, drop: float, buffer: float) -> tuple[int, int]:
    ok = 0
    for owner, matches in cases:
        pol = IsolationPolicy(owner, threshold, window_buffer=buffer, window_drop=drop)
        ok += pol.windows_ok(matches)
    return ok, len(cases)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="voiceroom.bench.windows")
    p.add_argument("--data", default="data/LibriSpeech")
    p.add_argument("--subset", default="dev-clean")
    p.add_argument("--embedder", default="wespeaker-resnet34")
    p.add_argument("--out", default="reports")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--trials", type=int, default=30)
    p.add_argument("--buffers", type=float, nargs="+", default=BUFFERS)
    p.add_argument("--win-frames", type=int, default=WIN_FRAMES, help="32 frames = 1 s")
    a = p.parse_args(argv)
    root = Path(a.data)
    if not (root / a.subset).is_dir():
        print("bench.windows: need data/LibriSpeech (scripts/fetch_data.py)", file=sys.stderr)
        return 2
    emb = get_embedder(a.embedder)
    res = collect(
        LibriSpeechCorpus(root, a.subset), emb, a.trials, a.seed, a.win_frames, a.win_frames // 2
    )
    header = {
        "dataset": f"LibriSpeech {a.subset}",
        "config": f"trials={a.trials} seed={a.seed} window={a.win_frames} frames "
        f"hop={a.win_frames // 2}",
    }
    out = (
        Path(a.out)
        / f"windows-{emb.name}{'' if a.win_frames == WIN_FRAMES else f'-w{a.win_frames}'}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(emb.name, res, threshold_for(emb.name), a.buffers, header))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
