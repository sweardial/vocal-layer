# T-006 summary
**Changed:** `scripts/fetch_models.py` (sha256-pinned, idempotent, `--only`, writes models/README with CC-BY/MIT notices); `embedders/wespeaker.py` (onnxruntime + kaldi-native-fbank, registered `wespeaker-resnet34`); smoke script; deps onnxruntime, kaldi-native-fbank.
**Review fixes:** fbank window set to hamming (was knf default povey, cosine 0.938 vs correct); inputs <1 s padded (model gave NaN on short input); smoke now tests short real audio and same-vs-different speaker ordering.
**Verified:**
- `uv run python scripts/fetch_models.py` -> both models ok (sha256 match).
- `uv run python scripts/smoke/smoke_embedder_wespeaker.py` -> RTF=0.055, same=0.992 diff=0.794, pass.
- `make check` -> 6 scripts, 0 failed.
**Not done (LOW):** input shape validation; default model path assumes source install.
**Note:** hamming window is from memory of wespeaker's compute_fbank (no offline copy to confirm).
