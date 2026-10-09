"""Smoke: encrypted cohort file -> AS-norm Matcher -> Pipeline (isolation + collaboration)."""

import asyncio
import stat
import tempfile
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet

from voiceroom.adapters import MockProvider
from voiceroom.audio import iter_frames
from voiceroom.cohort import Cohort, estimate_score_scale, load_default
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import make_conversation, split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.matcher import Matcher
from voiceroom.pipeline import Pipeline
from voiceroom.policy import UNKNOWN_LABEL, CollaborationPolicy, IsolationPolicy, Verdict
from voiceroom.segmenter import Segmenter
from voiceroom.vad import FRAME_SIZE, EnergyVAD

corpus = SynthCorpus(4, seed=2, utts_per_speaker=12)
spks = corpus.speakers()
enrolled_names, stranger = spks[:3], spks[3]
owner = enrolled_names[0]
emb = get_embedder("toy")
enroll_utts, held = split_utterances(corpus, spks, n_enroll=6)
sigs = [enroll(s, np.concatenate([u.load() for u in enroll_utts[s]]), emb) for s in enrolled_names]

# cohort: other synthetic speakers (disjoint seed), embeddings only
other = SynthCorpus(30, seed=77, utts_per_speaker=2)
vecs, owners = [], []
for s in other.speakers():
    for u in other.utterances(s):
        vecs.append(emb.embed(u.load()[:48000]))
        owners.append(s)
vecs = np.stack(vecs)
vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
scale = estimate_score_scale(vecs, owners)
cal = Matcher(sigs, threshold=0.0, cohort=Cohort(vecs, emb.name, 0.0, 100, scale))
gen, imp = [], []
for s in spks:
    for u in held[s]:
        sc = cal.identify_embedding(emb.embed(u.load()[:16000])).scores
        gen += [sc[s]] if s in enrolled_names else []
        imp += [v for k, v in sc.items() if k != s or s == stranger]
thr = (float(np.percentile(gen, 5)) + float(np.percentile(imp, 95))) / 2
co = Cohort(vecs, emb.name, thr, 100, scale, tuple(other.speakers()))
assert "array" not in repr(co) and "0." not in repr(co).split("threshold")[0], repr(co)

key = Fernet.generate_key()
with tempfile.TemporaryDirectory() as d:
    path = Path(d) / f"cohort-{emb.name}.npz"
    co.save(path, key)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    try:
        np.load(path, allow_pickle=False)
        raise SystemExit("cohort file is readable without the key")
    except ValueError:
        pass
    try:
        Cohort.load(path, Fernet.generate_key())
        raise SystemExit("wrong key accepted")
    except ValueError:
        pass
    loaded = load_default(emb.name, key, Path(d))
assert loaded is not None and loaded.speakers == co.speakers and loaded.threshold == thr


def run(policy, seed):
    conv = make_conversation(
        corpus, spks, n_turns=10, pause_range=(0.6, 1.2), snr_db=None, seed=seed, n_enroll=6
    )
    provider = MockProvider()
    pipe = Pipeline(
        EnergyVAD(),
        Segmenter(),
        Matcher(sigs, embedder=emb, cohort=loaded),  # threshold comes from the cohort
        policy,
        provider,
        max_pending=1000,
        isolation="thread",
    )
    asyncio.run(pipe.run(iter_frames(conv.audio, FRAME_SIZE)))
    return conv, pipe, provider


def truth(conv, r):
    return max(conv.segments, key=lambda s: min(s[1], r.t_end) - max(s[0], r.t_start))[2]


iso = IsolationPolicy(owner, loaded.threshold, early_score_buffer=0.02, early_margin=0.02)
n_owner = 0
for seed in (11, 12):
    conv, pipe, prov = run(iso, seed)
    acc = [r for r in pipe.records if r.verdict is Verdict.ACCEPT]
    assert len(acc) == len(prov.turns)
    assert all(truth(conv, r) == owner for r in acc), "non-owner committed"
    assert all(t.speaker_label == owner for t in prov.turns)
    n_owner += len(acc)
assert iso.score_scale == scale
assert n_owner, "no owner turn committed"

# collaboration built with an embedder (cosine default threshold) must adopt the cohort's
col = CollaborationPolicy(enrolled_names, "label", embedder=emb.name)
ok = n = 0
labels = []
for seed in (11, 12):
    conv, pipe, prov = run(col, seed)
    accepted = [r for r in pipe.records if r.verdict is Verdict.ACCEPT]
    for r, t in zip(accepted, prov.turns, strict=True):
        tr = truth(conv, r)
        ok += t.speaker_label == (UNKNOWN_LABEL if tr == stranger else tr)
        n += 1
        labels.append(t.speaker_label)
assert col.threshold == loaded.threshold and col.score_scale == scale, (col.threshold, scale)
assert col.threshold - col.window_buffer * col.score_scale > 0  # continuity floor is in z-units
assert set(labels) <= set(enrolled_names) | {UNKNOWN_LABEL}
assert UNKNOWN_LABEL in labels, "stranger not attributed Unknown"
assert n and ok >= 0.9 * n, (ok, n)
print(f"e2e cohort ok (isolation commits={n_owner}, collab {ok}/{n})")
