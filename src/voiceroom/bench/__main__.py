"""CLI: python -m voiceroom.bench --corpus synth|librispeech --embedder NAME [--out reports]."""

import argparse

from voiceroom.bench.report import write_report
from voiceroom.bench.run import build_trials
from voiceroom.embedders import get_embedder
from voiceroom.eval.corpus import LibriSpeechCorpus, SynthCorpus


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="voiceroom.bench")
    p.add_argument("--corpus", default="synth", choices=["synth", "librispeech"])
    p.add_argument("--data", default="data/LibriSpeech", help="LibriSpeech root")
    p.add_argument("--subset", default=None)
    p.add_argument("--embedder", default="toy")
    p.add_argument("--out", default="reports")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--conversations", type=int, default=12)
    p.add_argument("--speakers", type=int, default=3, help="speakers per conversation")
    p.add_argument("--turns", type=int, default=8)
    p.add_argument("--enroll-utts", type=int, default=None)
    p.add_argument("--snr-db", type=float, default=None)
    p.add_argument("--threshold", type=float, default=None, help="default: EER threshold")
    a = p.parse_args(argv)

    if a.corpus == "synth":
        corpus = SynthCorpus(6, seed=a.seed, utts_per_speaker=12)
        n_enroll = a.enroll_utts or 5
        desc = f"synthetic (6 speakers, seed {a.seed})"
    else:
        corpus = LibriSpeechCorpus(a.data, a.subset)
        n_enroll = a.enroll_utts or 3
        desc = f"LibriSpeech {a.subset or a.data}"
    emb = get_embedder(a.embedder)
    trials = build_trials(
        corpus, emb, n_conversations=a.conversations, n_speakers=a.speakers, n_turns=a.turns,
        n_enroll=n_enroll, snr_db=a.snr_db, seed=a.seed,
    )  # fmt: skip
    header = {
        "dataset": desc,
        "seed": a.seed,
        "config": f"conversations={a.conversations} speakers/conv={a.speakers} "
        f"turns={a.turns} enroll_utts={n_enroll} snr_db={a.snr_db}",
    }
    path = write_report(a.out, emb.name, trials, header, a.threshold)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
