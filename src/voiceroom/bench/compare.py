"""Compare scoring variants for noisy/short accuracy (T-024, D-011).

python -m voiceroom.bench.compare --embedder wespeaker-resnet34 [--n-conversations 30]
Every variant gets its own threshold, calibrated on clean >= 1 s audio at --target-far, and is then
judged at that threshold on clean and 10 dB audio. Writes reports/compare-<embedder>.md.
"""

import argparse
from pathlib import Path

import numpy as np

from voiceroom import scoring
from voiceroom.audio import SAMPLE_RATE
from voiceroom.bench import metrics as M
from voiceroom.config import WINDOW_S
from voiceroom.embedders import embed_batch, get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import make_conversation, split_utterances
from voiceroom.eval.corpus import LibriSpeechCorpus

AUG_SNRS = (20.0, 10.0, 5.0)
CONDS = {"clean": None, "10 dB": 10.0}
CUTS = (0.5, 1.0, 1.5)


def _cohort_by_speaker(corpus, emb, n_enroll, seed):
    spks = corpus.speakers()
    enroll_utts, _ = split_utterances(corpus, spks, n_enroll, seed)
    win = int(WINDOW_S * SAMPLE_RATE)
    out = {}
    for s in spks:
        audio = np.concatenate([u.load() for u in enroll_utts[s]]).astype(np.float32)
        clips = [audio[o : o + win] for o in range(0, len(audio) - win + 1, win)][:6]
        out[s] = embed_batch(emb, clips)
    return out


def collect(corpus, emb, n_conv, n_speakers, n_turns, n_enroll, seed):
    all_spk = corpus.speakers()
    cohorts = _cohort_by_speaker(corpus, emb, n_enroll, seed)
    rng = np.random.default_rng([seed, 7])
    convs = []
    for c in range(n_conv):
        pick = np.random.default_rng([seed, 11, c]).choice(len(all_spk), n_speakers, replace=False)
        spks = [all_spk[i] for i in sorted(pick)]
        enroll_utts, _ = split_utterances(corpus, spks, n_enroll, seed)
        sigs = {}
        for s in spks:
            a = np.concatenate([u.load() for u in enroll_utts[s]]).astype(np.float32)
            sigs[s] = (enroll(s, a, emb, ()), enroll(s, a, emb, AUG_SNRS))
        cohort = np.concatenate([v for k, v in cohorts.items() if k not in spks])
        items = []  # (cond, speaker, duration, embedding)
        for cond, snr in CONDS.items():
            conv = make_conversation(
                corpus, spks, n_turns, snr_db=snr, seed=seed * 1000 + c, n_enroll=n_enroll,
                split_seed=seed,
            )  # fmt: skip
            for st, en, spk in conv.segments:
                seg = conv.audio[round(st * SAMPLE_RATE) : round(en * SAMPLE_RATE)]
                clips = [seg]
                for cl in CUTS:
                    n = round(cl * SAMPLE_RATE)
                    if n < len(seg):
                        o = int(rng.integers(len(seg) - n + 1))
                        clips.append(seg[o : o + n])
                items += [(cond, spk, len(cp) / SAMPLE_RATE, emb.embed(cp)) for cp in clips]
        convs.append({"sigs": sigs, "cohort": cohort, "items": items})
        print(f"conv {c + 1}/{n_conv}", flush=True)
    return convs


def _variants():
    def cent(x, base, aug, cohort, norm, use_aug, mode):
        sig = aug if use_aug else base
        if mode == "topk":
            s = scoring.topk_chunk_score(x, sig.chunks, 3)
        elif mode == "kmeans":
            s = float(np.max(scoring.kmeans_centroids(sig.chunks, 3) @ x))
        else:
            s = scoring.centroid_score(x, sig.centroid)
        return scoring.as_norm(s, x, sig.centroid, cohort) if norm else s

    names = {
        "baseline (centroid)": (False, False, "centroid"),
        "multi-centroid (top-3 chunks)": (False, False, "topk"),
        "multi-centroid (3 k-means)": (False, False, "kmeans"),
        "AS-norm": (True, False, "centroid"),
        "noise-aug enrollment": (False, True, "centroid"),
        "noise-aug + AS-norm": (True, True, "centroid"),
        "noise-aug + top-3 chunks": (False, True, "topk"),
        "noise-aug + top-3 + AS-norm": (True, True, "topk"),
    }
    return {n: (lambda *a, p=p: cent(*a, *p)) for n, p in names.items()}


def score_all(convs):
    res = {}
    for name, fn in _variants().items():
        rows = []  # cond, label, dur, score
        for cv in convs:
            for cond, spk, dur, x in cv["items"]:
                for owner, (base, aug) in cv["sigs"].items():
                    rows.append((cond, owner == spk, dur, fn(x, base, aug, cv["cohort"])))
        res[name] = rows
    return res


def _sel(rows, cond, long):
    r = [x for x in rows if x[0] == cond and (x[2] >= 1.0) == long]
    return np.array([x[3] for x in r]), np.array([x[1] for x in r], bool)


def report(res, target_far, header):
    pct = lambda v: f"{100 * v:.2f}%"  # noqa: E731
    lines = [
        "# Scoring variants: noisy / short-utterance comparison",
        "",
        *[f"- **{k}**: {v}" for k, v in header.items()],
        f"- **threshold**: per variant, lowest with clean >= 1 s FAR <= {pct(target_far)}",
        "",
        "| variant | threshold | clean >=1 s FAR | clean >=1 s FRR | 10 dB >=1 s FAR "
        "| **10 dB >=1 s FRR** | 10 dB >=1 s EER | clean <1 s FRR | 10 dB <1 s FRR |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, rows in res.items():
        s, y = _sel(rows, "clean", True)
        thr = M.threshold_for_far(s, y, target_far)
        cells = [name, f"{thr:.3f}"]
        a, b = M.far_frr(s, y, thr)
        cells += [pct(a), pct(b)]
        s2, y2 = _sel(rows, "10 dB", True)
        a, b = M.far_frr(s2, y2, thr)
        cells += [pct(a), f"**{pct(b)}**", pct(M.eer(s2, y2)[0])]
        for cond in ("clean", "10 dB"):
            ss, yy = _sel(rows, cond, False)
            cells.append(pct(M.far_frr(ss, yy, thr)[1]))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(prog="voiceroom.bench.compare")
    p.add_argument("--embedder", default="wespeaker-resnet34")
    p.add_argument("--data", default="data/LibriSpeech")
    p.add_argument("--subset", default="dev-clean")
    p.add_argument("--out", default="reports")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--n-conversations", type=int, default=30, dest="n_conv")
    p.add_argument("--target-far", type=float, default=0.01)
    a = p.parse_args(argv)
    corpus = LibriSpeechCorpus(Path(a.data), a.subset)
    convs = collect(corpus, get_embedder(a.embedder), a.n_conv, 3, 8, 5, a.seed)
    header = {
        "dataset": f"LibriSpeech {a.subset}, seed {a.seed}, {a.n_conv} conversations",
        "embedder": a.embedder,
        "noise": "test: white gaussian; enrollment augmentation: pink/brown at "
        f"{AUG_SNRS} dB (different noise type on purpose)",
        "cohort": "enrollment windows of speakers outside the conversation, AS-norm top-100",
    }
    text = report(score_all(convs), a.target_far, header)
    out = Path(a.out) / f"compare-{a.embedder}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
