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
