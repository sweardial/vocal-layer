# Review T-013: streaming VAD

`make check`: green (11 scripts; smoke_vad.py 2.4 s; it ran the Silero branch because the model and LibriSpeech data are present locally).

I checked the Silero v5 wrapper (input/state/sr names, (2,1,128) state, 64-sample context, 512-sample frames) against the upstream OnnxWrapper and found no problems. I probed the smoke metric with degenerate VADs (probe in /tmp, not committed):

| VAD on smoke conversation | smoke metric (forgiven) | raw frame agreement |
|---|---|---|
| synth, always-off | 0.114 | 0.114 |
| synth, always-on | 0.886 | 0.886 |
| synth, EnergyVAD | 0.995 | 0.995 |
| libri, always-on | **0.904 (passes ≥ 0.90)** | 0.904 |
| libri, SileroVAD | 1.000 | 0.766 |
| libri, SileroVAD with state reset every frame (broken) | **1.000** | 0.558 |
| libri, EnergyVAD | 1.000 | 0.701 |

## Findings

[HIGH] scripts/smoke/smoke_vad.py:22-49,63-67: the Silero acceptance check passes trivially. `forgive_pauses` counts every VAD-off run of up to 1.5 s inside a ground-truth segment as correct. Most onset and offset misses and internal dropouts therefore never count, and only false positives in the inter-turn gaps can fail the check. The gaps are about 9.5 % of this conversation's frames. → Failure scenario: a VAD that always returns 1.0 scores 0.904 and passes. A SileroVAD that drops its recurrent state (the "state carried across frames" acceptance item) scores 1.000, the same as the correct implementation. Its real speech-frame miss rate is 49 % (vs 26 %). A regression in state handling, context handling, or the model would ship green. The determinism assertion at lines 69-72 doesn't help, because a stateless model is also deterministic. → Fix: score speech frames honestly. Drop the blanket forgiveness, or limit it to a short collar (for example ±2-3 frames at segment edges). Assert gap false-positive rate and in-segment recall separately, with thresholds that the always-on/always-off baselines and the stateless variant fail. For example, gap FP ≤ 10 % and speech recall ≥ 0.70: raw recall is 0.74 for Silero and 0.51 when stateless. If the backlog's "≥ 90 % frame agreement" can't be met honestly on read speech, record that in the summary and adjust the metric explicitly. Don't hide the gap with forgiveness. Also add a direct state check: after warming on speech, the output for a fixed frame must differ from the output after `reset()`.

[LOW] scripts/smoke/smoke_vad.py:52-56: the synthetic EnergyVAD check has little margin against a degenerate VAD. Speech is 88.6 % of the frames, so always-on scores 0.886 against the 0.90 bar, and the gaps are exact digital zeros. → Failure scenario: a mis-tuned EnergyVAD (for example a `low_db` so low that it fires on any noise floor) still passes, because the synthetic gaps contain no noise. T-014/T-016 then inherit an untested threshold. → Fix: use longer pauses or a non-None `snr_db` in `make_conversation` (for example 20 dB), so the gaps contain noise and always-on fails clearly. Also assert gap FP rate separately as above.

[LOW] src/voiceroom/vad.py:27-31: `_check` silently casts any dtype to float32. → Failure scenario: a mic `frame_source` in T-016/T-021 opens sounddevice with `dtype="int16"` (common) and feeds frames straight in. EnergyVAD returns 1.0 for every non-silent frame (verified: an int16 frame of value 8000 gives 1.0), and Silero gets input at ±32767 instead of [-1, 1]. Turns never end and no error is raised. D-004 says int16 is only for I/O boundaries. → Fix: raise when `not np.issubdtype(frame.dtype, np.floating)` and point to `audio.pcm16_to_float`.

No other issues found. There is no logging of audio or probabilities, no unsafe deserialization (the ONNX model is sha256-pinned in fetch_models.py), and no provider code. Per-frame inference is one single-threaded ORT run with the session created once, and buffers are fixed-size.
