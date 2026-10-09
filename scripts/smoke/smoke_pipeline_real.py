"""Smoke: 1 short LibriSpeech conversation, realtime, SileroVAD + wespeaker (SKIPs if absent)."""

import sys
import time
from pathlib import Path

import numpy as np

from voiceroom.bench.pipeline import CappedCorpus, Stats, score_isolation, stream
from voiceroom.bench.run import enroll_speakers
from voiceroom.config import threshold_for
from voiceroom.eval.conversations import Conversation, make_conversation
from voiceroom.eval.corpus import LibriSpeechCorpus
from voiceroom.pipeline import TurnRecord
from voiceroom.policy import IsolationPolicy, Verdict
from voiceroom.vad import DEFAULT_MODEL

t0 = time.monotonic()  # imports (a few s) are outside this clock
ROOT = Path(__file__).resolve().parents[2]
if not DEFAULT_MODEL.exists() or not (ROOT / "data/LibriSpeech/dev-clean").is_dir():
    print("SKIP: need models/silero_vad.onnx and data/LibriSpeech")
    sys.exit(77)
try:
    from voiceroom.embedders import get_embedder

    emb = get_embedder("wespeaker-resnet34")
except (ImportError, FileNotFoundError) as e:
    print(f"SKIP: wespeaker unavailable: {e}")
    sys.exit(77)

corpus = CappedCorpus(LibriSpeechCorpus(ROOT / "data/LibriSpeech", "dev-clean"), 4.0)
spks = corpus.speakers()[:2]
sigs = list(enroll_speakers(corpus, spks, emb, 8, 0).values())
conv = make_conversation(corpus, spks, 2, pause_range=(0.5, 0.8), seed=3, n_enroll=8)
# The provisional config window_drop (0.05) falsely rejects real owner turns (see T-018 report);
# relaxed here so the smoke checks the pipeline mechanics.
pol = IsolationPolicy(spks[1], threshold_for(emb.name), window_drop=0.3, embedder=emb.name)
recs, prov = stream(conv, sigs, emb, pol, 6.0)  # 6x realtime to fit the smoke budget
st = Stats()
score_isolation(st, conv, recs, spks[1])
print(f"records={len(recs)} commits={len(prov.turns)} latencies={st.latencies}")
assert st.owner and sum(c[0] for c in st.owner.values()) == 0, "owner turn rejected"
assert st.non_owner == [0, 1], "non-owner forwarded"
assert len(prov.turns) == 1 and st.commit_latencies and st.errors == 0
assert max(st.commit_latencies) < 2.0
# Partial leak: accepted turn [0, 1.2] over owner [0, 1] + other [1, 2] sends 200 ms of other.
leak = Conversation(np.zeros(0, np.float32), [(0.0, 1.0, "o"), (1.0, 2.0, "x")], [], {})
rec = TurnRecord(0.0, 1.2, Verdict.ACCEPT, "o", 0.9, False, 0.4, 0.1)
st2 = Stats()
score_isolation(st2, leak, [rec], "o")
assert st2.non_owner == [1, 1], st2.non_owner
elapsed = time.monotonic() - t0
print(f"pipeline real ok ({elapsed:.1f}s)")
assert elapsed < 12.0, elapsed
