"""Smoke: 1 short multi-track LibriSpeech conversation through RoomPipeline (SKIPs if absent)."""

import sys
from pathlib import Path

import numpy as np

from voiceroom.bench import room as R
from voiceroom.bench.pipeline import CappedCorpus
from voiceroom.vad import DEFAULT_MODEL

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

from voiceroom.eval.corpus import LibriSpeechCorpus  # noqa: E402

corpus = CappedCorpus(LibriSpeechCorpus(ROOT / "data/LibriSpeech", "dev-clean"), 6.0)
emb.embed(np.zeros(16000, np.float32))
# 4x realtime for the time budget
st = R.build(corpus, emb, 1, 6, 1, 4.0, True, None, 1, n_enroll=8, sweep=True)
text = R.render(emb.name, {"bleed": st}, {"dataset": "smoke"}, 0.0, 0.3)
assert "Verify threshold sweep" in text and "owner FR" in text, text
assert st.records > 0 and st.attrib, st
assert st.sweep_owner, "no owner segments scored"
print(f"records={st.records} owner={st.owner} imp={st.imp} dup={st.dup} attrib={st.attrib}")
