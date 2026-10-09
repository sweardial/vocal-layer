# Decisions

## D-001 (2026-10-08) How we prove changes while unit tests are off
- What: Every feature gets one smoke script in `scripts/smoke/`. Smoke scripts run offline on *synthetic voices* (`voiceroom.synth`) with a numpy-only `toy` embedder and an `EnergyVAD`. Checks that need real models or LibriSpeech print `SKIP: <reason>` and exit 0 when the assets are missing from `models/` or `data/`. Assets are fetched only by `scripts/fetch_models.py` and `scripts/fetch_data.py`, never by `make check`.
- Why: `make check` must be offline, fast (< 60 s), and green from a clean checkout. Real-model accuracy is measured by `make bench`, not by smoke scripts.
- Alternatives: commit small audio fixtures (not allowed: no audio in git); download in checks (not allowed: no network).

## D-002 (2026-10-08) Speaker-embedder candidates
- What: Pluggable `Embedder` protocol. Candidates:
  1. **WeSpeaker ResNet34-LM, ONNX** (`Wespeaker/wespeaker-voxceleb-resnet34-LM`, `voxceleb_resnet34_LM.onnx`, about 26.5 MB, CC-BY-4.0 so attribution is required, Vox1-O EER about 0.72–0.87 %). It runs on `onnxruntime` (1.30 has cp312 wheels) with an 80-dim Kaldi fbank front end. Use `kaldi-native-fbank` if it has a cp312 wheel, otherwise a numpy port. The implementer must verify the fbank config against the model's wespeaker config.
  2. **SpeechBrain ECAPA-TDNN** (`speechbrain/spkrec-ecapa-voxceleb`, Apache-2.0, about 83 MB, EER about 0.8 %). It is an optional extra `ecapa` because it needs torch (CPU wheels via the uv index). Known risk: torchaudio ≥ 2.9 breakage in speechbrain (`list_audio_backends`, issue #3012). Pin compatible torch/torchaudio versions and load audio with soundfile ourselves.
  - Possible third: 3D-Speaker CAM++ ONNX (unverified weights/licence). It is only a follow-up if the first two miss the targets.
- Why: WeSpeaker ONNX avoids torch at runtime and is small, which suits CPU realtime. ECAPA is the strong, well-known baseline.
- Final choice and threshold: decided by `make bench` in T-012 and recorded below.

## D-003 (2026-10-08) VAD
- What: Silero VAD as ONNX through `onnxruntime` directly (no torch). The model file is fetched by `scripts/fetch_models.py`. Frames are 512 samples (32 ms) at 16 kHz, and the implementer must verify this against the silero code. An `EnergyVAD` with the same interface is used for synthetic smoke checks.
- Why: Silero is the de-facto CPU VAD, and the ONNX path keeps torch out of the core install.
- Alternatives: webrtcvad (weaker, unmaintained wheels), and the `silero-vad` pip package (pulls in torch).

## D-004 (2026-10-08) Audio formats and libraries
- What: Internally, audio is **16 kHz mono float32 numpy** in [-1, 1]. int16 PCM is used only at I/O and provider boundaries. File I/O uses `soundfile` (wheels bundle libsndfile). Resampling uses `scipy.signal.resample_poly`. Mic/speaker use `sounddevice` (PortAudio is bundled on macOS and is `libportaudio2` in the container). It is imported lazily so that headless environments work.
- Why: These are few, standard, wheel-available dependencies.

## D-005 (2026-10-08) Evaluation data
- What: LibriSpeech **dev-clean** (SLR12, 337 MB, 40 speakers). `scripts/fetch_data.py` downloads it with mirror fallback (openslr.trmal.net, openslr.elda.org, us.openslr.org), verifies it against `md5sum.txt`, and extracts it to `data/LibriSpeech/`. `--mini` uses dev-clean-2 (SLR31, 126 MB) for quick runs. Noise is synthetic (white, pink, or brown at a target SNR).
- Why: dev-clean is small and has enough speakers for EER. MUSAN is 11 GB. ESC-50 is about 600 MB and CC BY-NC.

## D-006 (2026-10-08) Signature store
- What: There is one file per signature, encrypted with `cryptography` **Fernet** (authenticated). The key comes from `VOICEROOM_KEY`, or from a keyfile (default `~/.voiceroom/key`, created with mode 0600, never in the repo). Each record holds the embeddings, the embedder name/version, consent info and a timestamp. It never holds raw audio. Delete removes the file. Reprs and logs never include embeddings.
- Why: Fernet is simple and misuse-resistant for small blobs, and MultiFernet gives key rotation later. AESGCM needs nonce management.

## D-007 (2026-10-08) OpenAI Realtime adapter
- What: Use the GA Realtime API over WebSocket (`wss://api.openai.com/v1/realtime?model=...`, default model `gpt-realtime-2.1`, configurable, with no `OpenAI-Beta` header). Send `session.update` with `session.type="realtime"`, `session.audio.input.turn_detection=null`, and `audio/pcm` (24 kHz only), so the adapter resamples 16 kHz→24 kHz. Each accepted turn is sent as `[conversation.item.create input_text "[Speaker: X]" in collaboration mode]` → `input_audio_buffer.append`… → `input_audio_buffer.commit` → `response.create`. Output is `response.output_audio.delta`. Barge-in uses `response.cancel` plus `conversation.item.truncate`. The adapter is verified offline against a local fake WebSocket server. If the `openai` SDK is used, its realtime extra needs `websockets<16`.
- Uncertain: the exact `input_text` content shape for the attribution item. Verify it in the docs when implementing.
- Why: We own turn-taking (SPEC). Provider types never leave `adapters/`.

## D-008 (2026-10-08) ECAPA optional extra: pinned versions and workarounds
- What: extra `ecapa` = `torch==2.8.0`, `torchaudio==2.8.0`, `speechbrain==1.0.3` (imports cleanly on 3.12/aarch64 linux; torchaudio 2.8 is the last release that still has `list_audio_backends`, which speechbrain calls at import, so >= 2.9 is avoided). torch/torchaudio come from the PyTorch CPU index through `[tool.uv.sources]` (explicit index, linux/win32 markers; macOS uses PyPI wheels). Import emits harmless deprecation warnings.
- Workarounds: we bypass `EncoderClassifier`/Pretrainer (HF download, symlinks) and build `Fbank -> InputNormalization(sentence) -> ECAPA_TDNN` directly, loading only `embedding_model.ckpt` (rev-pinned, sha256-verified, Apache-2.0) into `models/`. Audio comes in as numpy, so no torchaudio I/O backend is used. `mean_var_norm_emb`/classifier are unused (as in `encode_batch` with normalize=False).
- Note: plain `uv run` (no `--extra ecapa`) removes torch from the venv, so the smoke SKIPs; use `uv run --extra ecapa` or `uv sync --extra ecapa`.

## D-009 (2026-10-08) Embedder and threshold
- What: default embedder **wespeaker-resnet34** (ONNX, no torch), cosine threshold **0.35** (ECAPA fallback 0.32), `MIN_MARGIN` 0, enrollment ≥ 10 s speech in 3 s windows / 1.5 s hop (5 LibriSpeech utterances in the bench), minimum decision length 1 s (`MIN_DECISION_S`; defined here, consumed by the M1 pipeline, not yet enforced). All in `voiceroom/config.py`; `Matcher(threshold=None)` and `enroll` read it; `make bench` reports at these thresholds.
- Numbers (`reports/bench-*.md`, LibriSpeech dev-clean, 30 convs, 960 target / 1920 non-target trials):
  - Clean EER: wespeaker **3.33 %**, ECAPA 4.71 %. ≥ 1 s EER: wespeaker 0.69 %, ECAPA 1.11 %.
  - Wespeaker at 0.35, clean, ≥ 1 s: **FAR 1.18 %** (target < 2 %), **FRR 0.28 %** (target < 5 %). Both met. (At the old EER point 0.299, ≥ 1 s FAR was 3.96 %, which misses; hence the higher threshold. Side runs with `--threshold` 0.38 / 0.42 gave ≥ 1 s FAR 0.49 % / 0.07 % and FRR 0.69 % / 1.94 %.) ECAPA at 0.32 (its clean 1 % overall-FAR point) also meets the clean targets (≥ 1 s FAR 1.25 %, FRR 1.11 %) but is worse on < 1 s (FRR 37.9 % vs 18.8 %) and 10 dB EER (11.46 % vs 10.10 %).
  - Short (< 1 s), clean, at 0.35: EER 7.08 %, FRR 18.75 %, FAR 0.42 %. Not covered by the targets; reported separately.
  - 10 dB white noise, at 0.35: overall FAR 1.46 %, FRR 28.44 %; ≥ 1 s FAR 1.88 %, **FRR 16.94 %**; ≥ 1 s EER 6.67 %.
  - Latency: p95 per-clip embedding 595 ms (p50 ~75 ms; clean run, includes first-call/long-clip outliers); RTF 0.060 (`reports/bench-wespeaker-resnet34.md`). ECAPA: p95 500 ms, RTF 0.056. Both are far below real time, but p95 ≈ 0.6 s eats into the early-decision budget.
- Why wespeaker: lower EER in every condition, no torch at runtime, 26 MB, comparable speed.
- Gap: the FA/FR targets are met for clean audio only. With 10 dB noise ≥ 1 s FRR is 16.9 % (> 5 %), and < 1 s FRR is high in all conditions. The p95 latency is also high. Follow-ups added: T-024 (noise robustness: score norm / multi-centroid / noisy enrollment), T-025 (p95 latency), T-026 (CAM++ comparison).
- Caveat: LibriSpeech is read, clean speech; the threshold should be re-checked on live mic audio (T-023).

## D-010 (2026-10-08) OpenAI Realtime wire details (settles D-007 uncertainty)
- What: raw `websockets` client (no `openai` SDK). Attribution item = `{"type":"message","role":"user","content":[{"type":"input_text","text":"[Speaker: X]"}]}` sent before append/commit. Audio is appended in 100 ms pcm16 LE 24 kHz chunks. Reply audio is emitted at 24 kHz (`AudioOut.sample_rate=24000`); the sink resamples. `conversation.item.truncate.audio_end_ms` is clamped to the received audio length.
- Why: fewer dependencies, and the fake server speaks the same JSON. The input_text shape follows the GA client-events reference (developers.openai.com/api/reference/resources/realtime/client-events).
- Alternatives: the `openai` SDK realtime client; a single item with `input_text` + `input_audio` (kept as a fallback if attribution turns out weak live).

## D-011 (2026-10-08) Noise / short-utterance variants (T-024)
- What: compared 8 scoring variants (`python -m voiceroom.bench.compare`, `reports/compare-wespeaker-resnet34.md`; wespeaker, LibriSpeech dev-clean, 30 convs, white noise at 10 dB). Each variant's threshold is calibrated on clean ≥ 1 s at FAR ≈ 1 %, then judged on 10 dB.
- Numbers (10 dB, ≥ 1 s FRR at FAR ≤ ~1 %): baseline 18.2 %; k-means 3 centroids 16.7 %; top-3 chunks 17.8 %; AS-norm 16.5 %; noise-aug enrollment 14.0 % (FAR 1.6 %); **noise-aug + AS-norm 12.4 % (FAR 0.97 %)**; noise-aug + top-3 + AS-norm 21.1 %. < 1 s 10 dB FRR: 72.5 % baseline → 58.3 % best.
- Wired behind config (all **off by default**): `ENROLL_NOISE_SNRS` (e.g. `(20, 10, 5)`; pink/brown noise, deliberately not the white noise used in testing) and `Matcher(cohort=...)` → AS-norm with `ASNORM_THRESHOLDS` (wespeaker 2.88).
- Target NOT met: best 10 dB ≥ 1 s FRR 12.4 % vs < 5 %. Off by default because gain is moderate, AS-norm needs a cohort file (not yet shipped/loaded by the pipeline), and enrollment costs 4x embeds.
- Caveats: one seed, ~720 ≥ 1 s targets so ±1-2 pt noise; cohort comes from the same corpus; noise is synthetic white.
- AS-norm scores are z-units: the 2.88 threshold holds only for a bench-like cohort, and IsolationPolicy's cosine-scale early buffers must be rescaled before it is enabled in the pipeline (T-027).
- Next: stronger levers are likely a noise-robust embedder (T-026 CAM++) or denoising front-end; re-check on live audio (T-023).
