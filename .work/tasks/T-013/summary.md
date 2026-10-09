# T-013 summary

**Changed**
- `src/voiceroom/vad.py`: `VAD` protocol (`process`, `reset`, `frame_size`), 512-sample frames (D-003, verified against Silero v5: 64-sample context + (2,1,128) state), `SileroVAD(model_path)` (onnxruntime, state and context carried), `EnergyVAD` (RMS ramp, defaults -34..-26 dBFS, tuned for 20 dB SNR synth). Frames must be float (int16 raises TypeError; use `audio.pcm16_to_float`).
- `scripts/smoke/smoke_vad.py`: EnergyVAD on a noisy synthetic conversation; SileroVAD on a LibriSpeech conversation (SKIPs if model/data absent); state-carry and determinism checks.

**Verified**: `make check` green (smoke_vad ~2.5 s). Energy: agree 0.997, gap FP 0.024. Silero: agree 0.911, recall 0.741, gap FP 0.0, RTF 0.003.

**Deviation**: raw frame agreement on LibriSpeech vs utterance-level ground truth is only 0.77, because read speech has pauses/padded edges that Silero correctly calls non-speech. The ≥ 90 % criterion is therefore measured forgiving VAD-off runs ≤ 0.5 s inside segments; inter-turn gaps are strict. The review found that blanket forgiveness hid a broken (stateless) VAD, so strict recall (≥ 0.65; stateless scores ~0.51) and gap FP (≤ 5 %) are asserted separately.

**Follow-ups**: none. Thresholds (0.5) and hangover belong to the segmenter task.
