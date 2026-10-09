"""CLI: python -m voiceroom.bench --corpus synth|librispeech --embedder NAME|all [--snr DB ...]."""

import argparse
import sys
import time
from pathlib import Path

from voiceroom.bench.report import write_report
from voiceroom.bench.run import build_trials
from voiceroom.embedders import get_embedder
from voiceroom.eval.corpus import LibriSpeechCorpus, SynthCorpus

REAL_EMBEDDERS = ("wespeaker-resnet34", "ecapa-voxceleb")
DEFAULT_SNR = 10.0


def _fail(msg: str) -> int:
    print(f"bench: {msg}", file=sys.stderr)
    return 2


def _load_embedder(name: str, explicit: bool):
    """Return an embedder, or None to skip it when running `all` and it is not installed."""
    try:
        return get_embedder(name)
    except (ImportError, FileNotFoundError) as e:
        # with `all`, only a not-installed optional package (ImportError) is skippable
        if explicit or name == REAL_EMBEDDERS[0] or isinstance(e, FileNotFoundError):
            extra = " (ECAPA also needs `uv run --extra ecapa`)" if "ecapa" in name else ""
            msg = f"embedder {name!r} unavailable: {e}. Run scripts/fetch_models.py{extra}."
            raise SystemExit(_fail(msg)) from e
        print(f"bench: skipping {name}: {e}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="voiceroom.bench")
    p.add_argument("--corpus", default="synth", choices=["synth", "librispeech"])
    p.add_argument("--data", default="data/LibriSpeech", help="LibriSpeech root")
    p.add_argument("--subset", default="dev-clean")
    p.add_argument("--embedder", default="toy", help="name, or 'all' = installed real embedders")
    p.add_argument("--out", default="reports")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--n-conversations", "--conversations", type=int, default=30, dest="n_conv")
    p.add_argument("--speakers", type=int, default=3, help="speakers per conversation")
    p.add_argument("--turns", type=int, default=8)
    p.add_argument("--enroll-utts", type=int, default=None)
    p.add_argument(
        "--snr", "--snr-db", type=float, nargs="*", default=None, dest="snr",
        help=f"noisy conditions in dB (default {DEFAULT_SNR:g}); a clean run is always included",
    )  # fmt: skip
    p.add_argument("--threshold", type=float, default=None, help="default: clean EER threshold")
    a = p.parse_args(argv)
    snrs = [DEFAULT_SNR] if a.snr is None else a.snr

    if a.corpus == "synth":
        corpus = SynthCorpus(6, seed=a.seed, utts_per_speaker=12)
        n_enroll = a.enroll_utts or 5
        desc = f"synthetic (6 speakers, seed {a.seed})"
    else:
        root = Path(a.data)
        if not (root / a.subset).is_dir():
            return _fail(f"LibriSpeech {root / a.subset} not found. Run scripts/fetch_data.py.")
        corpus = LibriSpeechCorpus(root, a.subset)
        n_enroll = a.enroll_utts or 5
        desc = f"LibriSpeech {a.subset} ({len(corpus.speakers())} speakers)"
        if len(corpus.speakers()) < a.speakers:
            return _fail(f"{root / a.subset} has too few speakers. Run scripts/fetch_data.py.")

    names = list(REAL_EMBEDDERS) if a.embedder == "all" else [a.embedder]
    for name in names:
        emb = _load_embedder(name, explicit=a.embedder != "all")
        if emb is None:
            continue
        t0 = time.monotonic()
        conds = {"clean": None, **{f"{s:g} dB SNR": s for s in snrs}}
        trials = {
            label: build_trials(
                corpus, emb, n_conversations=a.n_conv, n_speakers=a.speakers, n_turns=a.turns,
                n_enroll=n_enroll, snr_db=snr, seed=a.seed,
            )
            for label, snr in conds.items()
        }  # fmt: skip
        header = {
            "dataset": desc,
            "seed": a.seed,
            "noise": "white gaussian, SNR measured over speech samples",
            "config": f"conversations={a.n_conv} speakers/conv={a.speakers} "
            f"turns={a.turns} enroll_utts={n_enroll} conditions={list(conds)}",
        }
        path = write_report(a.out, emb.name, trials, header, a.threshold)
        print(f"{path} ({time.monotonic() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
