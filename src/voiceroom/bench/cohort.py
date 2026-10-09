"""Build the shipped AS-norm cohort file (T-027, D-013).

python -m voiceroom.bench.cohort --embedder wespeaker-resnet34 [--data data/LibriSpeech]
Speakers are split in two: half supply the cohort windows (embeddings only, no audio is stored),
the other half calibrate the AS-norm threshold (FAR ~ --target-far over clean 2 s probes) and
report the genuine accept rate. Writes data/cohort-<embedder>.npz and reports/cohort-<embedder>.md.
"""

import argparse
from pathlib import Path

import numpy as np

from voiceroom import scoring
from voiceroom.audio import SAMPLE_RATE
from voiceroom.cohort import Cohort, default_path, estimate_score_scale
from voiceroom.config import ASNORM_TOP_N, WINDOW_S
from voiceroom.embedders import embed_batch, get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import split_utterances
from voiceroom.eval.corpus import LibriSpeechCorpus
from voiceroom.store import load_key

PROBE_S = 2.0


def _windows(utts, win, limit):
    audio = np.concatenate([u.load() for u in utts]).astype(np.float32)
    return [audio[o : o + win] for o in range(0, len(audio) - win + 1, win)][:limit]


def build(corpus, emb, seed=0, n_enroll=5, target_far=0.01):
    spks = corpus.speakers()
    order = np.random.default_rng([seed, 27]).permutation(len(spks))
    half = len(spks) // 2
    coh_spk = [spks[i] for i in order[:half]]
    cal_spk = [spks[i] for i in order[half:]]
    enroll_utts, test_utts = split_utterances(corpus, spks, n_enroll, seed)
    win = int(WINDOW_S * SAMPLE_RATE)
    clips, owners = [], []
    for s in coh_spk:
        w = _windows(enroll_utts[s], win, 6)
        clips += w
        owners += [s] * len(w)
    cohort = embed_batch(emb, clips).astype(np.float32)
    cohort /= np.linalg.norm(cohort, axis=1, keepdims=True)

    sigs = {
        s: enroll(s, np.concatenate([u.load() for u in enroll_utts[s]]), emb, ()) for s in cal_spk
    }
    probes = {
        s: embed_batch(emb, _windows(test_utts[s], int(PROBE_S * SAMPLE_RATE), 8)) for s in cal_spk
    }
    gen, imp = [], []
    for s, sig in sigs.items():
        for t, ps in probes.items():
            for p in ps:
                p = p / np.linalg.norm(p)
                z = scoring.as_norm(float(p @ sig.centroid), p, sig.centroid, cohort, ASNORM_TOP_N)
                (gen if s == t else imp).append(z)
    thr = float(np.quantile(imp, 1 - target_far))
    scale = estimate_score_scale(cohort, owners)
    stats = {
        "cohort_windows": len(cohort),
        "cohort_speakers": len(coh_spk),
        "calibration_speakers": len(cal_spk),
        "threshold": thr,
        "far": float((np.array(imp) >= thr).mean()),
        "tar": float((np.array(gen) >= thr).mean()),
        "score_scale": scale,
    }
    return Cohort(cohort, emb.name, thr, ASNORM_TOP_N, scale, tuple(coh_spk)), stats


def main(argv=None):
    p = argparse.ArgumentParser(prog="voiceroom.bench.cohort")
    p.add_argument("--embedder", default="wespeaker-resnet34")
    p.add_argument("--data", default="data/LibriSpeech")
    p.add_argument("--subset", default="dev-clean")
    p.add_argument("--out", default="reports")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--target-far", type=float, default=0.01)
    a = p.parse_args(argv)
    emb = get_embedder(a.embedder)
    c, st = build(LibriSpeechCorpus(Path(a.data), a.subset), emb, a.seed, 5, a.target_far)
    path = default_path(emb.name)
    c.save(path, load_key())
    lines = [
        f"# AS-norm cohort: {emb.name}",
        "",
        f"file: `{path}` (embeddings only, encrypted with the signature-store key)",
        "",
    ]
    lines += [f"- {k}: {v:.4g}" if isinstance(v, float) else f"- {k}: {v}" for k, v in st.items()]
    text = "\n".join(lines) + "\n"
    out = Path(a.out) / f"cohort-{emb.name}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
