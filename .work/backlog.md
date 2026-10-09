# Backlog

Conventions (see `.work/decisions.md` D-001): smoke scripts live in `scripts/smoke/smoke_*.py`, run offline, and print `SKIP: …` and exit 0 when real models or data are absent. Synthetic voices plus the `toy` embedder plus `EnergyVAD` are the offline stand-ins.

## M0: Speaker-ID core + benchmark

- [x] T-001 | M0 | Set up project skeleton and smoke runner
  goal: Create a uv-managed Python 3.12 package `voiceroom` (src layout) with ruff and a smoke runner so `make check` is green.
  accept: `pyproject.toml` (uv, requires-python >=3.12, ruff config, deps numpy/scipy/soundfile), `src/voiceroom/__init__.py` with `__version__`, `scripts/smoke/run_all.py` runs every `scripts/smoke/smoke_*.py` in a subprocess, prints PASS/FAIL/SKIP with timing, and exits non-zero if any fails or the total exceeds 60 s; `scripts/smoke/smoke_import.py` imports voiceroom; existing Makefile targets kept (`bench` may still be a stub); `uv sync && make check` passes; summary shows that a deliberately failing smoke script makes `make smoke` exit non-zero (then removed).
  depends: none

- [x] T-002 | M0 | Add audio utilities and synthetic speaker generator
  goal: Provide 16 kHz mono float32 audio helpers and deterministic synthetic "voices" so every later smoke check runs offline.
  accept: `voiceroom/audio.py` (load any wav/flac → 16 kHz mono float32, resample via scipy, float↔int16 PCM, fixed-size frame iterator) and `voiceroom/synth.py` (seeded speaker = f0/formant/jitter profile → utterance of given duration; white/pink/brown noise at target SNR); `scripts/smoke/smoke_audio.py` checks round-trip resample length/dtype, frame iterator coverage, same seed → identical audio, different speakers → different spectra, and noise SNR within ±1 dB; `make check` green.
  depends: T-001

- [x] T-003 | M0 | Define pluggable embedder interface with toy embedder
  goal: Define the `Embedder` protocol and registry plus a numpy-only `toy` embedder (log-mel statistics) for offline checks.
  accept: `voiceroom/embedders/base.py` (`name`, `dim`, `embed(audio16k) -> L2-normalised np.ndarray`, optional `embed_batch`), `voiceroom/embedders/__init__.py` `get_embedder(name)` registry that lazily imports optional backends, `embedders/toy.py`; `scripts/smoke/smoke_embedder_toy.py` asserts that on 4 synthetic speakers the mean same-speaker cosine exceeds the mean different-speaker cosine by a clear margin (≥ 0.1) and that output is unit-norm and deterministic; `make check` green.
  depends: T-002

- [x] T-004 | M0 | Implement enrollment signatures and cosine matcher
  goal: Turn ~20–30 s of enrollment audio into a `Signature` and identify a segment against enrolled signatures with a threshold and margin.
  accept: `voiceroom/enroll.py` (`enroll(name, audio, embedder) -> Signature`: chunk into windows, embed, store centroid plus chunk embeddings, reject < 10 s of speech) and `voiceroom/matcher.py` (`Matcher(signatures, threshold).identify(audio|embedding) -> Match(name|None, score, margin)`, scores for all candidates); `Signature.__repr__` never shows embeddings; `scripts/smoke/smoke_enroll_match.py` enrolls 3 synthetic speakers with the toy embedder, identifies 30 held-out 2 s utterances with ≥ 90 % accuracy, and rejects a 4th unenrolled speaker in most cases (printed rates); `make check` green.
  depends: T-003

- [x] T-005 | M0 | Add encrypted file-based signature store with delete
  goal: Persist signatures encrypted at rest (Fernet), with list/load/delete and consent metadata, per decision D-006.
  accept: `voiceroom/store.py` (`SignatureStore(root, key)` with save/load/list/delete; key from `VOICEROOM_KEY` or keyfile created 0600); `scripts/smoke/smoke_store.py` in a temp dir: round-trip equality, file bytes do not contain the raw float32 embedding bytes or the plaintext name, a wrong key raises a clear error, delete removes the file and the listing, and the keyfile mode is 0600; no embeddings in logs or reprs; `make check` green.
  depends: T-004

- [x] T-006 | M0 | Add model fetch script and WeSpeaker ONNX embedder
  goal: Download pinned model files to `models/` and add the WeSpeaker ResNet34-LM embedder running on onnxruntime with a Kaldi-compatible fbank.
  accept: `scripts/fetch_models.py` (idempotent, sha256-pinned, downloads `voxceleb_resnet34_LM.onnx` and the Silero VAD ONNX into `models/`, `--only` flag); `embedders/wespeaker.py` registered as `wespeaker-resnet34` (fbank params verified against the wespeaker config and noted in the plan; CC-BY attribution in a `models/README` or NOTICE written by the fetch script); `scripts/smoke/smoke_embedder_wespeaker.py` SKIPs if the model is absent, otherwise checks dim 256, unit norm, determinism, and RTF < 0.2 on 5 s audio; the script is run after `fetch_models.py` with the result recorded in summary.md; `make check` green.
  depends: T-003

- [x] T-007 | M0 | Add LibriSpeech download script and corpus index
  goal: Fetch LibriSpeech dev-clean (or dev-clean-2 with `--mini`) into `data/` and index utterances by speaker.
  accept: `scripts/fetch_data.py` (mirror fallback, md5 check, resumable or skip-if-present, extract to `data/LibriSpeech/`); `voiceroom/eval/corpus.py` (`Corpus` protocol: speakers → utterances with duration; `LibriSpeechCorpus(root)`; `SynthCorpus(n_speakers, seed)` built on `synth`); `scripts/smoke/smoke_corpus.py` always checks SynthCorpus and checks LibriSpeechCorpus (≥ 40 speakers for dev-clean) only when data is present, otherwise SKIP for that part; the fetch is run once in the container with counts recorded in summary.md; `make check` green.
  depends: T-002

- [x] T-008 | M0 | Build synthetic conversations with ground-truth segments
  goal: Generate reproducible multi-speaker "conversations" (2–4 speakers, pauses, optional noise) with ground-truth segments from any Corpus.
  accept: `voiceroom/eval/conversations.py` (`make_conversation(corpus, speakers, n_turns, pause_range, snr_db|None, seed) -> Conversation(audio, segments[(start, end, speaker)])`, plus enrollment/test utterance split so enrollment audio never appears in conversations, and save/load as wav + JSON under `data/`); `scripts/smoke/smoke_conversations.py` on SynthCorpus checks that segments do not overlap and are sorted, segment audio matches the source utterances, pauses are within range, the same seed gives identical output, and the enrollment/test split is disjoint; `make check` green.
  depends: T-007

- [x] T-009 | M0 | Add optional SpeechBrain ECAPA embedder
  goal: Add `ecapa` as a second embedder candidate behind an optional `ecapa` extra (torch CPU), loading audio ourselves to avoid torchaudio backend issues.
  accept: pyproject optional extra `ecapa` using the torch CPU index via `[tool.uv]` sources, with torch/torchaudio/speechbrain versions pinned to a combination that imports cleanly on 3.12; `embedders/ecapa.py` registered as `ecapa-voxceleb` (model cached under `models/`, fetched via `fetch_models.py --only ecapa`); `scripts/smoke/smoke_embedder_ecapa.py` SKIPs when the extra or model is missing, otherwise checks dim 192, unit norm, and determinism; core `uv sync` (without the extra) still passes `make check`; the pinned versions and any workaround go in decisions.md.
  depends: T-006

- [x] T-010 | M0 | Implement bench metrics, trials, and report writer
  goal: Compute speaker-verification metrics from scored trials and write the markdown report SPEC requires.
  accept: `voiceroom/bench/metrics.py` (EER with its threshold, FAR/FRR at a given threshold, threshold for a target FAR, length buckets <1 s / 1–2 s / 2–4 s / ≥4 s, latency p50/p95, RTF) and `voiceroom/bench/report.py` (writes `reports/bench-<embedder>.md` with all sections plus dataset/seed/config header); `voiceroom/bench/run.py` builds trials from conversation segments (target = owner, non-target = others) against enrolled signatures and times each segment embedding; `scripts/smoke/smoke_bench.py` runs the bench with `--corpus synth --embedder toy` in < 10 s into a temp dir and asserts every report section exists and EER is in [0, 0.5]; `make check` green.
  depends: T-005, T-008

- [x] T-011 | M0 | Wire make bench to LibriSpeech for all embedders
  goal: Make `make bench` run end to end on LibriSpeech dev-clean for every installed real embedder and write one report per embedder.
  accept: `make bench` (Makefile and `python -m voiceroom.bench` CLI with `--embedder`, `--corpus`, `--n-conversations`, `--snr`, `--seed`) exits with a clear message pointing to `fetch_data.py`/`fetch_models.py` when assets are missing, otherwise writes `reports/bench-wespeaker-resnet34.md` (and `reports/bench-ecapa-voxceleb.md` if the extra is installed) including clean and noisy (e.g. 10 dB SNR) results; it runs in the container and the reports are committed; bench runtime is noted in summary.md; `make check` green.
  depends: T-006, T-009, T-010

- [x] T-012 | M0 | Choose embedder and threshold from bench results
  goal: Pick the default embedder, threshold, and enrollment/early-decision parameters from the bench and justify them against the SPEC targets.
  accept: a `.work/decisions.md` entry "D-0xx Embedder and threshold" citing numbers from `reports/bench-*.md` (EER, FAR/FRR at the chosen threshold vs targets FA < 2 % and FR < 5 % for ≥ 1 s, short-utterance results, p95 latency, RTF), with the chosen defaults set in `voiceroom/config.py` and read by matcher/enroll; if targets are missed, the entry states the gap and adds follow-up tasks (e.g. multi-centroid signatures, score norm, CAM++) at the end of the backlog; `make bench` and `make check` green.
  depends: T-011

## M1: Live isolation with a realtime model

- [x] T-013 | M1 | Add streaming VAD with Silero ONNX and energy fallback
  goal: Provide a frame-level streaming `VAD` interface with a Silero ONNX implementation and an `EnergyVAD` for synthetic audio.
  accept: `voiceroom/vad.py` (`VAD.process(frame) -> speech_prob`, `reset()`, frame size per D-003; `SileroVAD(model_path)` via onnxruntime with state carried across frames; `EnergyVAD`); `scripts/smoke/smoke_vad.py` checks that EnergyVAD on a synthetic conversation reaches ≥ 90 % frame agreement with ground truth, and that SileroVAD (SKIP if model or data is absent) reaches ≥ 90 % frame agreement on one LibriSpeech conversation with RTF < 0.1; `make check` green.
  depends: T-008, T-006

- [x] T-014 | M1 | Implement segmenter with turn endpointing and early decision hooks
  goal: Turn VAD frame decisions into speech segments/turns with hangover-based endpointing, a max turn length, and an "enough audio to identify" event.
  accept: `voiceroom/segmenter.py` (streaming; emits `SpeechStart`, `IdentifyReady(audio_so_far)` after a configurable min duration such as 1.0 s, `TurnEnd(audio, t_start, t_end)` after configurable trailing silence such as 400 ms, pre-roll padding, max-turn split); `scripts/smoke/smoke_segmenter.py` streams a synthetic conversation through EnergyVAD and the segmenter and asserts that turn boundaries are within ±300 ms of ground truth for ≥ 90 % of turns and that no turn spans two speakers when pauses are ≥ 600 ms; `make check` green.
  depends: T-013

- [x] T-015 | M1 | Define provider adapter interface and MockProvider
  goal: Define the provider-agnostic async adapter interface and a `MockProvider` that records what was committed.
  accept: `voiceroom/adapters/base.py` (`Provider`: `connect()`, `send_turn(audio16k, speaker_label|None)`, `interrupt()`, `audio_out` async iterator or callback, `close()`, with provider-agnostic types only) and `voiceroom/adapters/mock.py` (records turns, labels, and timestamps, and emits a short synthetic reply per turn); `scripts/smoke/smoke_mock_provider.py` sends 3 turns and checks the recorded order, labels, reply audio, and interrupt; a grep check in the smoke script asserts no provider-specific imports outside `adapters/`; `make check` green.
  depends: T-001

- [x] T-016 | M1 | Build streaming pipeline with isolation policy
  goal: Wire frames → VAD → segmenter → identify (early decision when confident) → isolation policy → provider, and measure end-of-speech → commit latency.
  accept: `voiceroom/policy.py` (`IsolationPolicy(owner)`) and `voiceroom/pipeline.py` (async `Pipeline(vad, segmenter, matcher, policy, provider).run(frame_source)`; decides early on `IdentifyReady` when score/margin is confident, otherwise at `TurnEnd`; drops rejected audio without forwarding it; records per-turn decision and latency); `scripts/smoke/smoke_e2e_isolation.py` runs 3 synthetic conversations (owner plus 2 others, toy embedder, EnergyVAD, MockProvider) and asserts zero non-owner turns committed, ≥ 90 % owner turns committed, and prints latency p50/p95; `make check` green.
  depends: T-004, T-014, T-015

- [x] T-017 | M1 | Add collaboration mode with speaker attribution
  goal: Forward all enrolled speakers with correct labels, and apply the unknown-speaker policy (drop or label "Unknown").
  accept: `CollaborationPolicy(enrolled, unknown="drop"|"label")` in `policy.py`, with the pipeline passing the label to `send_turn`; `scripts/smoke/smoke_e2e_collab.py` runs synthetic conversations with 3 enrolled speakers plus 1 unknown and asserts that every committed turn's label matches ground truth (≥ 95 %) and that unknown turns are dropped under "drop" and labeled "Unknown" under "label"; `make check` green.
  depends: T-016

- [x] T-018 | M1 | Measure pipeline latency and isolation accuracy on real components
  goal: Report end-of-speech→commit latency and isolation FA/FR for the full pipeline with SileroVAD, the chosen embedder, and LibriSpeech conversations.
  accept: `python -m voiceroom.bench.pipeline` (also run by `make bench`) streams N LibriSpeech conversations at simulated realtime pacing through the pipeline with MockProvider and writes `reports/pipeline-<embedder>.md` (latency p50/p95 vs the 700 ms target, owner FR by length bucket, non-owner FA, collaboration attribution accuracy); `scripts/smoke/smoke_pipeline_real.py` runs 1 short conversation in < 15 s or SKIPs if assets are absent; the report is committed and its key numbers are in summary.md; `make check` green.
  depends: T-012, T-017

- [x] T-019 | M1 | Implement OpenAI Realtime adapter against a local fake server
  goal: Implement the OpenAI Realtime GA adapter per D-007 (manual turns, attribution text item, 24 kHz resampling, output audio, cancel), verified offline.
  accept: `voiceroom/adapters/openai_realtime.py` implementing `Provider` (the API key comes only from the env and is never logged; model configurable); the plan cites the current official docs for every event used; `scripts/smoke/fake_realtime_server.py` (localhost websockets server that records client events and replies with `response.output_audio.delta`/`response.done`); `scripts/smoke/smoke_openai_adapter.py` asserts `session.update` with `turn_detection` null, each committed turn is append…commit→response.create, the attribution item precedes audio in collaboration mode, no events are sent for dropped turns, audio deltas are decoded to the output stream, and interrupt sends `response.cancel`; `scripts/live/openai_realtime_check.py` (manual, not in make check) sends a wav turn and saves the reply to `data/out/`; `make check` green.
  depends: T-015

- [x] T-020 | M1 | Add mic and speaker I/O with file-based stand-ins
  goal: Provide audio sources/sinks: sounddevice mic input and speaker output (lazy import), plus file source and wav sink for headless runs.
  accept: `voiceroom/io.py` (`MicSource` 16 kHz frames via sounddevice InputStream into an asyncio queue, `SpeakerSink` playing provider audio with resampling, `FileSource` with realtime or fast pacing, `WavSink`, `list_devices()` that degrades gracefully with no devices); `scripts/smoke/smoke_io.py` runs FileSource → pipeline (MockProvider) → WavSink on a synthetic conversation and checks the output wav exists and is non-empty, and checks `list_devices()` does not crash in the container; `make check` green.
  depends: T-016

- [x] T-021 | M1 | Add voiceroom CLI for enroll, list, delete, and demo
  goal: Expose `voiceroom enroll --name alice [--file x.wav]`, `voiceroom list`, `voiceroom delete`, and `voiceroom demo --mode isolation|collaboration --owner alice` per SPEC.
  accept: a `[project.scripts] voiceroom` entry point; `enroll` records ~25 s from the mic after an explicit consent prompt (or `--file`, with `--yes` for non-interactive consent) and saves to the encrypted store; `demo` takes `--provider openai|mock`, `--input file.wav` (otherwise mic), `--output-device`/`--out wav`, and `--embedder` (default from config), and prints one line per turn (speaker, decision, latency) without audio or embeddings; `scripts/smoke/smoke_cli.py` runs via subprocess with `--embedder toy` on synthetic wavs: enroll ×2 → list → demo isolation with mock on a conversation wav (asserts only owner turns are reported committed) → delete → list empty; `make check` green.
  depends: T-005, T-019, T-020

- [ ] T-022 | M1 | Support barge-in: stop playback when accepted speaker talks
  goal: When an accepted speaker starts talking while the model is responding, stop local playback and interrupt the provider; ignore non-accepted speakers.
  accept: The pipeline issues `provider.interrupt()` and flushes the sink once a turn by an accepted speaker is confirmed during playback (OpenAI adapter: `response.cancel` + `conversation.item.truncate` with played ms). Non-owner speech never interrupts in isolation mode. `scripts/smoke/smoke_bargein.py` uses MockProvider with a long reply and checks two cases: an owner turn mid-reply triggers exactly one interrupt and a sink flush, and a non-owner turn triggers none. `make check` green.
  depends: T-021

- [ ] T-023 | M1 | Write macOS demo checklist and request human live test
  goal: Write exact macOS steps for the human to verify enrollment, isolation, collaboration, and the live OpenAI path with a real mic and speaker.
  accept: `docs/demo-macos.md` covers: prerequisites (uv, Python 3.12, `OPENAI_API_KEY`); `uv sync`; `fetch_models.py`; `voiceroom enroll` ×2; `voiceroom demo --mode isolation --owner alice --provider openai` with a script of who says what and the expected outcome; the collaboration run; `scripts/live/openai_realtime_check.py`; headphones advice to avoid echo; what to report back. Write `.work/tasks/T-023/needs_human.md` with the checklist link and a short results form. `make check` green.
  depends: T-018, T-022
  human: Run the macOS demo checklist with a real mic and speaker and an OpenAI API key, then report the results.

- [x] T-024 | M0 | Improve noisy and short-utterance accuracy
  goal: Close the gap from D-009: at 10 dB SNR ≥ 1 s FRR is 16.9 % (target < 5 % at FAR < 2 %); < 1 s FRR is 18.8 % clean.
  accept: bench compares at least two of: multi-centroid signatures, score normalization (AS-norm/cohort), noise-augmented enrollment; the best is wired behind config and `reports/` shows ≥ 1 s 10 dB FRR and FAR; a new D-0xx records the result; `make bench` and `make check` green.
  depends: T-012

- [ ] T-025 | M0 | Reduce p95 embedding latency
  goal: Per-clip embedding p95 is about 595 ms (p50 75 ms) for wespeaker; find the cause (long clips, fbank, onnx threads, first calls) and cut it.
  accept: bench report shows p95 < 250 ms for the default embedder with no accuracy change (EER within 0.2 pt); `make bench` and `make check` green.
  depends: T-012

- [ ] T-026 | M0 | Evaluate CAM++ as an embedder candidate
  goal: Try 3D-Speaker CAM++ ONNX (verify licence/weights) against wespeaker on the bench.
  accept: a CAM++ embedder in the registry (or a decision recording why not) and its bench report; D-0xx updated if it beats wespeaker; `make bench` and `make check` green.
  depends: T-012
- [ ] T-027 | M1 | Ship AS-norm cohort and decide noise-aug enrollment defaults
  goal: D-011 variants are config-only; add a cohort file loader (data/, no audio) and set defaults after live-audio check (T-023).
  accept: pipeline uses cohort when present, with threshold stored alongside the cohort and IsolationPolicy early buffers rescaled to the AS-norm scale; bench shows no clean regression; D-0xx updated.
  depends: T-024
- [ ] T-028 | M1 | Calibrate speaker-continuity window parameters on bench audio
  goal: The pipeline's ~0.5 s window continuity check (`IsolationPolicy.windows_ok`; `WINDOW_DROPS`, `window_buffer`) uses provisional values for wespeaker/ecapa. Calibrate on LibriSpeech conversations with mid-turn speaker changes.
  accept: bench or script reports interior-interjection leak rate vs owner false-reject (turns >= 1 s) for wespeaker at candidate `window_drop` values; config values set from it and recorded in decisions.md; `make check` green.
  depends: T-016

- [ ] T-028 | M1 | Collaboration: split turns on mid-turn speaker change instead of dropping
  goal: When continuity fails in collaboration mode, split the turn at the change point and forward each part with its own label.
  accept: smoke with A->B no-pause turn forwards both parts labeled correctly; `make check` green.
  depends: T-017

- [ ] T-029 | M1 | Calibrate pipeline continuity check and endpointing on real audio
  goal: Owner FR is 86 % in `reports/pipeline-wespeaker-resnet34.md` (non-owner FA 0 %): the provisional `WINDOW_DROPS` (0.05) / `window_buffer` falsely reject real owner turns, and read-speech pauses split ~24 segments into ~39 turns (segmenter end_silence 0.4 s). Calibrate window_drop/window_buffer and segmenter settings on LibriSpeech, re-run `python -m voiceroom.bench.pipeline`, target owner FR < 5 % at FA < 2 %.
  accept: new pipeline report committed with owner FR (>= 1 s) < 5 % and attribution accuracy >= 90 %; smoke_pipeline_real uses config defaults (no window_drop override); `make check` green.
  depends: T-018

- [ ] T-030 | M1 | Speed up pipeline bench enrollment and trim smoke total
  goal: `python -m voiceroom.bench.pipeline` takes ~9 min for 4 conversations (enrollment per conversation + 2 realtime passes) and `make smoke` is ~66 s (thin margin after T-018). Cache signatures per speaker set, and shorten the slowest smokes (smoke_identify_isolation 15 s).
  accept: bench < 5 min, `make smoke` < 60 s with `make check` green.
  depends: T-018
