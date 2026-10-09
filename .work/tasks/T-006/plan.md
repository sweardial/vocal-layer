# T-006 plan
1. scripts/fetch_models.py: pinned URL+sha256 table (WeSpeaker HF rev, Silero tag), `--only`, .part+verify+rename, writes models/README (CC-BY / MIT notices).
2. embedders/wespeaker.py: onnxruntime CPU, knf fbank. Params checked against wespeaker `compute_fbank` (torchaudio kaldi): 80 mels, 25/10 ms, dither 0, snip_edges, waveform*32768, then per-utterance CMN (no variance norm), 16 kHz.
3. Register `wespeaker-resnet34`; deps onnxruntime, kaldi-native-fbank.
4. Inputs < 1 s are padded (model yields NaN on very short input).
5. Smoke: dim 256, unit norm, determinism, RTF < 0.2, short-input finite; SKIP (exit 77) when model absent.
6. Fbank options in full: hamming window (review fix; knf default is povey), use_energy=False, snip_edges=True, dither 0, preemph 0.97/remove_dc_offset/round_to_power_of_two (knf defaults = torchaudio kaldi defaults), 80 mels, low_freq 20, high_freq 0 (Nyquist).
