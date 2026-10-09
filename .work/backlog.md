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

- [x] T-022 | M1 | Support barge-in: stop playback when accepted speaker talks
  goal: When an accepted speaker starts talking while the model is responding, stop local playback and interrupt the provider; ignore non-accepted speakers.
  accept: The pipeline issues `provider.interrupt()` and flushes the sink once a turn by an accepted speaker is confirmed during playback (OpenAI adapter: `response.cancel` + `conversation.item.truncate` with played ms). Non-owner speech never interrupts in isolation mode. `scripts/smoke/smoke_bargein.py` uses MockProvider with a long reply and checks two cases: an owner turn mid-reply triggers exactly one interrupt and a sink flush, and a non-owner turn triggers none. `make check` green.
  depends: T-021

- [x] T-023 | M1 | Write macOS demo checklist and request human live test
  goal: Write exact macOS steps for the human to verify enrollment, isolation, collaboration, and the live OpenAI path with a real mic and speaker.
  accept: `docs/demo-macos.md` covers: prerequisites (uv, Python 3.12, `OPENAI_API_KEY`); `uv sync`; `fetch_models.py`; `voiceroom enroll` ×2; `voiceroom demo --mode isolation --owner alice --provider openai` with a script of who says what and the expected outcome; the collaboration run; `scripts/live/openai_realtime_check.py`; headphones advice to avoid echo; what to report back. Write `.work/tasks/T-023/needs_human.md` with the checklist link and a short results form. `make check` green.
  depends: T-018, T-022
  human: Run the macOS demo checklist with a real mic and speaker and an OpenAI API key, then report the results.

- [x] T-024 | M0 | Improve noisy and short-utterance accuracy
  goal: Close the gap from D-009: at 10 dB SNR ≥ 1 s FRR is 16.9 % (target < 5 % at FAR < 2 %); < 1 s FRR is 18.8 % clean.
  accept: bench compares at least two of: multi-centroid signatures, score normalization (AS-norm/cohort), noise-augmented enrollment; the best is wired behind config and `reports/` shows ≥ 1 s 10 dB FRR and FAR; a new D-0xx records the result; `make bench` and `make check` green.
  depends: T-012

- [x] T-025 | M0 | Reduce p95 embedding latency
  goal: Per-clip embedding p95 is about 595 ms (p50 75 ms) for wespeaker; find the cause (long clips, fbank, onnx threads, first calls) and cut it.
  accept: bench report shows p95 < 250 ms for the default embedder with no accuracy change (EER within 0.2 pt); `make bench` and `make check` green.
  depends: T-012

- [x] T-026 | M0 | Evaluate CAM++ as an embedder candidate
  goal: Try 3D-Speaker CAM++ ONNX (verify licence/weights) against wespeaker on the bench.
  accept: a CAM++ embedder in the registry (or a decision recording why not) and its bench report; D-0xx updated if it beats wespeaker; `make bench` and `make check` green.
  depends: T-012
- [x] T-027 | M1 | Ship AS-norm cohort and decide noise-aug enrollment defaults
  goal: D-011 variants are config-only; add a cohort file loader (data/, no audio) and set defaults after live-audio check (T-023).
  accept: pipeline uses cohort when present, with threshold stored alongside the cohort and IsolationPolicy early buffers rescaled to the AS-norm scale; bench shows no clean regression; D-0xx updated.
  depends: T-024
- [x] T-028 | M1 | Calibrate speaker-continuity window parameters on bench audio
  goal: The pipeline's ~0.5 s window continuity check (`IsolationPolicy.windows_ok`; `WINDOW_DROPS`, `window_buffer`) uses provisional values for wespeaker/ecapa. Calibrate on LibriSpeech conversations with mid-turn speaker changes.
  accept: bench or script reports interior-interjection leak rate vs owner false-reject (turns >= 1 s) for wespeaker at candidate `window_drop` values; config values set from it and recorded in decisions.md; `make check` green.
  depends: T-016

- [x] T-028 | M1 | Collaboration: split turns on mid-turn speaker change instead of dropping
  goal: When continuity fails in collaboration mode, split the turn at the change point and forward each part with its own label.
  accept: smoke with A->B no-pause turn forwards both parts labeled correctly; `make check` green.
  depends: T-017

- [x] T-029 | M1 | Calibrate pipeline continuity check and endpointing on real audio
  goal: Owner FR is 86 % in `reports/pipeline-wespeaker-resnet34.md` (non-owner FA 0 %): the provisional `WINDOW_DROPS` (0.05) / `window_buffer` falsely reject real owner turns, and read-speech pauses split ~24 segments into ~39 turns (segmenter end_silence 0.4 s). Calibrate window_drop/window_buffer and segmenter settings on LibriSpeech, re-run `python -m voiceroom.bench.pipeline`, target owner FR < 5 % at FA < 2 %.
  accept: new pipeline report committed with owner FR (>= 1 s) < 5 % and attribution accuracy >= 90 %; smoke_pipeline_real uses config defaults (no window_drop override); `make check` green.
  depends: T-018

- [x] T-030 | M1 | Speed up pipeline bench enrollment and trim smoke total
  goal: `python -m voiceroom.bench.pipeline` takes ~9 min for 4 conversations (enrollment per conversation + 2 realtime passes) and `make smoke` is ~66 s (thin margin after T-018). Cache signatures per speaker set, and shorten the slowest smokes (smoke_identify_isolation 15 s).
  accept: bench < 5 min, `make smoke` < 60 s with `make check` green.
  depends: T-018
- [x] T-031 | M1 | Rework continuity check: longer windows / softer rule to cut owner false-reject
  goal: D-014 shows 0.5 s wespeaker windows give 63-65 % owner FR at any `window_drop` (the absolute floor `threshold - window_buffer` and the `best == owner` test; buffer 0.2 already gives ~10 pt lower FR for wespeaker); 1 s windows (`python -m voiceroom.bench.windows --win-frames 32`) give ~22-28 % FR with ~0-2 % leak for >= 1 s interjections. Try WIN_FRAMES=32, require the owner best only on a fraction of windows, or compare to the turn mean score; recalibrate with the windows bench.
  accept: bench reports owner FR (turns >= 1 s) <= 10 % at leak <= 5 % for >= 1 s interior interjections, or documents why not; `make check` green.
  depends: T-028

- [x] T-032 | M1 | Cut sub-1 s interior-interjection leak without raising owner FR
  goal: Per D-015 owner decision, the shipped continuity rule (3 bad windows in a row + foreign-enrolled guard, drop 0.4) leaks 42 % of 0.5 s interjections at owner FR 10 % (windows bench). Build 1 s windows and/or VAD-aware splitting (split a turn at pauses/speaker change) to reduce that leak.
  accept: rerun `python -m voiceroom.bench.windows`: owner FR (turns >= 1 s) < 5 % and 0.5 s leak below 42 %, report committed; `make check` green.
  depends: T-029

- [x] T-033 | M1 | Add sounddevice as a dependency and fix demo docs
  goal: Human live test (T-023): `voiceroom enroll`/`demo` fail on a fresh Mac with "cannot record" because `sounddevice` is not a dependency. Add it (main dependency or an `audio` extra), update README and docs/demo-macos.md (remove the `uv run --with sounddevice` workaround note).
  accept: `uv sync` on a clean env provides sounddevice (import works); docs updated; `make check` green.
  depends: T-023
- [x] T-034 | M1 | Make the AS-norm cohort file portable across machines
  goal: Human live test (T-023): `data/cohort-wespeaker-resnet34.npz` is encrypted with the builder's key, so the demo fails elsewhere with "bad cohort file (cannot decrypt ...)". Store the cohort unencrypted (it holds no personal data) or build it per machine; then run the demo without `--no-cohort`.
  accept: cohort loads on a machine with a different key; smoke covers it; docs drop the `--no-cohort` note; `make check` green.
  depends: T-023
- [x] T-035 | M1 | Reduce owner false-reject on real mic audio
  goal: Human live test (T-023, wespeaker, `--no-cohort`): 3 of 8 alice-identified turns were dropped (1.15 s, 1.5 s, 0.16 s turns), i.e. owner false-reject on real MacBook mic audio. Investigate and tune (threshold, continuity rule, short-turn handling); also re-test narrowband (phone) voices.
  accept: documented analysis and a change or decision in decisions.md; bench numbers not regressed; `make check` green.
  depends: T-023, T-034
- [x] T-036 | M1 | Log which continuity rule fired in the live demo
  goal: T-035's cause of live owner false-reject is inferred. Make `voiceroom demo` (debug flag) log, per dropped turn, the rule that rejected it and the window scores (numbers only, no audio/embeddings); then a human reruns the demo.
  accept: smoke proves the log line on a synthetic turn; `make check` green.
  depends: T-035

## M3: LiveKit rooms, invites, multi-mic sessions

Context (see D-018): the pipeline is single-stream today. M3 adds a multi-source layer: one VAD and segmenter per participant track, a shared matcher, one provider, and serialized commits. Identity comes from the track and is *verified* by voice 1:1, because best-of-N identification on short real-mic turns is what caused owner false-rejects (T-023, D-014 to D-017). Integration code for LiveKit lives in `src/voiceroom/adapters/` (the provider-import grep in `smoke_mock_provider.py` already bans `livekit` elsewhere). Everything is built against an in-memory `FakeRoom` first, then a local `livekit-server --dev` (localhost only, no internet). Real devices and LiveKit Cloud come last.

- [x] T-037 | M3 | Free smoke-time headroom before M3 adds room smokes
  goal: `make smoke` is 40–59 s against the 60 s budget (T-030/T-036), and M3 adds 5–6 smokes, so cut the total well below the budget.
  accept: `scripts/smoke/run_all.py` prints a per-script time table sorted by duration. The slowest smokes are trimmed (fewer conversations, cached signatures, shared temp assets) or run concurrently via a `-j` option the Makefile uses. With real models and data present in the container, `make smoke` total is ≤ 35 s, recorded in summary.md as the median of 3 runs. No smoke loses its key assertion; each trimmed check is listed in the summary. `make check` green.
  depends: none

- [x] T-038 | M3 | Extract a per-source turn processor from Pipeline
  goal: Split `Pipeline` into a reusable per-source `SourceProcessor` (VAD → segmenter → identify → policy decision → TurnRecord) and the single-source wrapper, without behavior change, so a room can run one processor per track.
  accept: `voiceroom/pipeline.py` (or a new `voiceroom/source.py`) exposes `SourceProcessor(source_id, vad, segmenter, matcher, policy)` yielding decided turns (audio, label, decision, timings) without touching the provider. The existing `Pipeline.run` uses it and keeps its public signature. All existing smokes are unchanged and green. `python -m voiceroom.bench.pipeline` on the same seed reproduces owner FR, non-owner FA, attribution and commit p95 within ±1 turn / ±30 ms of `reports/pipeline-wespeaker-resnet34.md` (numbers in summary.md). `make check` green.
  depends: T-036

- [x] T-039 | M3 | Build multi-source room pipeline with serialized commits
  goal: Run N audio sources (added and removed at runtime) through their own SourceProcessors into one provider, committing accepted turns one at a time in end-of-speech order.
  accept: `voiceroom/room.py` has `RoomPipeline(provider, policy_factory, matcher, ...)` with `add_source(source_id, frames, claimed_identity=None)`, `remove_source(id)` (flushes the open turn), and `run()`. A commit queue guarantees that no provider `send_turn` calls interleave. Turns that end while another commit is in flight are queued and committed in order. Per-turn records include source_id and queue wait. `scripts/smoke/smoke_room_mux.py` streams 3 synthetic sources with interleaved and partly overlapping speech (toy embedder, EnergyVAD, MockProvider) and asserts every accepted turn is committed exactly once, in end-time order, with the right source, and that a source added mid-run and one removed mid-turn both behave. Smoke < 5 s. `make check` green.
  depends: T-038

- [x] T-040 | M3 | Add track-identity verification policy (verified and shared tracks)
  goal: Per source, either trust-but-verify the track's claimed identity with a 1:1 voice check, or (shared mic) identify by voice among session members with the existing collaboration rules.
  accept: `TrackPolicy` in `policy.py` has two modes. `verified`: compare against the claimed signature only and accept at a 1:1 threshold. A confident match to another member gives `mismatch` → relabel or drop (configurable). Otherwise the turn is unknown → the session's unknown policy. The continuity rule is relaxed to the short/lenient variant. `shared` reuses CollaborationPolicy. Thresholds are in `config.py` with a comment pointing at the bench that will calibrate them (T-042). `scripts/smoke/smoke_room_identity.py` (toy embedder) covers: an owner on their own track is accepted, including 1–2 s turns; another member speaking on alice's track is relabelled or dropped per config; a stranger on alice's track is dropped or labelled Unknown; and a shared-mic track with 2 members is attributed correctly. `make check` green.
  depends: T-039

- [ ] T-041 | M3 | Suppress cross-talk bleed between co-located microphones
  goal: When two participants' mics pick up the same speech (same room), commit it once, from the speaker's own track, instead of duplicating or mislabelling it.
  accept: `voiceroom/room.py` has a bleed detector over concurrent turns from different sources. It uses time overlap (≥ 50 %), level difference and voice match to the other track's claimed identity, and drops the copy on the quieter, non-matching track. It never drops a turn whose voice verifies as its own track's owner. `voiceroom.synth`/conversations gain a bleed option (a copy of speaker A in B's track at −10…−20 dB, 5–40 ms delay, with noise). `scripts/smoke/smoke_room_bleed.py` asserts: no duplicate commits at −12 dB bleed; true simultaneous speech by A and B on their own tracks yields two commits; and with bleed off, results match smoke_room_mux. `make check` green.
  depends: T-040

- [ ] T-042 | M3 | Benchmark room isolation and attribution on LibriSpeech multi-track audio
  goal: Measure verified-track owner FR, impostor-on-track catch rate, bleed duplicate rate and attribution with real models, then calibrate the T-040/T-041 thresholds.
  accept: `python -m voiceroom.bench.room` (run by `make bench`) builds multi-track LibriSpeech conversations (3 participants, optional bleed at −12 dB, impostor turns on a member's track, a shared-mic track, optional narrowband channel per D-017) and streams them through RoomPipeline with SileroVAD, wespeaker and MockProvider. It writes `reports/room-wespeaker-resnet34.md` with: owner FR on own track by length bucket (target < 5 % for ≥ 1 s), impostor accept rate, bleed duplicate rate, attribution accuracy, and commit latency p50/p95 including queue wait (target p95 < 700 ms). The verify threshold is set from it and recorded in a new D-0xx. Bench runtime < 5 min. `smoke_room_bench.py` runs a 1-conversation version or SKIPs if assets are absent. `make check` green.
  depends: T-040, T-041

- [ ] T-043 | M3 | Define room transport interface with in-memory FakeRoom
  goal: Add a provider-agnostic `RoomTransport` (participants joining and leaving, per-participant 16 kHz audio frames and attributes, publishing the agent's audio) plus `FakeRoom`, and drive RoomPipeline from it.
  accept: `voiceroom/rooms/base.py` has `RoomTransport` (async `connect`, an event stream of `ParticipantJoined(id, identity, attributes)` / `TrackAudio` iterators / `ParticipantLeft`, `publish_audio(AudioOut)`, `close`) with no LiveKit types. `voiceroom/rooms/fake.py` has `FakeRoom` (scripted participants from wavs or synth, records published audio). `voiceroom/rooms/runner.py` has `RoomAgent(transport, room_pipeline, provider)`, which maps join/leave to add/remove_source and the participant identity to claimed_identity, and plays provider audio into `publish_audio`. `scripts/smoke/smoke_room_fake.py` covers 2 participants joining at different times, one leaving mid-turn, correct labels in MockProvider, and published reply audio being non-empty. `make check` green.
  depends: T-039

- [ ] T-044 | M3 | Fetch a pinned local LiveKit server and prove an offline loopback
  goal: Make a local `livekit-server --dev` usable headless in the container, and verify the SDK facts D-018 leaves open: 16 kHz AudioStream resampling, two clients in one process, and no TURN needed on localhost.
  accept: `scripts/fetch_livekit.py` downloads a pinned `livekit-server` release (sha256-verified, linux x86_64/arm64 and macOS arm64) to `data/bin/`. Optional extra `livekit` adds pinned `livekit` and `livekit-api`. `voiceroom/adapters/livekit_server.py` has a context manager that starts the dev server on a free localhost port, waits for readiness and always kills it. `scripts/smoke/smoke_livekit_loopback.py` (SKIP if the binary or extra is absent) publishes a synthetic 2 s tone from client A and asserts client B receives ≥ 1.8 s of it at 16 kHz with the right participant identity, in < 10 s total. It is run in the container after fetching, with the result in summary.md. The verified answers (resampling, ports, one-process clients) go in D-018. `make check` green.
  depends: T-037

- [ ] T-045 | M3 | Implement LiveKit room transport against the local server
  goal: Implement `RoomTransport` on the LiveKit rtc SDK so the room agent joins as a bot, subscribes to every participant's audio, and publishes model audio as its own track.
  accept: `voiceroom/adapters/livekit_room.py` has `LiveKitRoom(url, token)`. It subscribes only to audio tracks, converts to 16 kHz mono via our `StreamResampler` (or the SDK if T-044 proved it), takes the participant identity and `voiceroom_user` attribute as the claimed identity, ignores its own track, and publishes 24 kHz provider audio through `rtc.AudioSource`. Credentials come from `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` (dev defaults only for localhost) and are never logged. `scripts/smoke/smoke_livekit_room.py` (SKIP if the binary or extra is absent) runs the local server, 2 publisher clients streaming synthetic speakers, and the bot with MockProvider, then asserts turns committed with the right labels and that a subscriber receives the bot's reply audio, in < 15 s. `make check` green.
  depends: T-043, T-044

- [ ] T-046 | M3 | Add sessions and invites with membership-driven policy
  goal: Let an owner create a session, invite another enrolled user with a signed expiring single-use code, accept or revoke it, and have membership drive who the room agent accepts.
  accept: `voiceroom/sessions.py` has `SessionStore(root, key)` (sessions: id, owner, mode isolation|collaboration, members, room name, unknown policy; encrypted like signatures). `create_session`, `invite(session, user) -> code` (HMAC-signed with key, expiry, single-use; fails if the user has no enrolled signature or consent), `accept(code)`, `revoke(session, user)`, `list`. `join_token(session, user)` uses livekit-api inside `adapters/` (identity=user, attribute `voiceroom_user`, room grant). A running RoomAgent picks up membership changes: a revoked member's turns become unknown and the participant is removed (FakeRoom; LiveKit RoomService kick is behind the adapter). Codes and tokens are never logged. `scripts/smoke/smoke_sessions.py` covers create → invite → accept → member accepted in FakeRoom; expired, reused, tampered and non-enrolled codes are rejected; revoke stops forwarding mid-session; in isolation mode, only the owner is forwarded even with members present. `make check` green.
  depends: T-040, T-043

- [ ] T-047 | M3 | Support barge-in and reply playback in rooms
  goal: Reuse M1 barge-in for rooms: an accepted participant who starts speaking during a model reply interrupts it. Non-accepted or bleed speech never does.
  accept: RoomAgent stops publishing, flushes the room AudioSource buffer, and calls `provider.interrupt(played_ms)` with the ms actually published, once an accepted turn is confirmed during playback. In rooms, the agent's own published audio is never treated as a participant source. `scripts/smoke/smoke_room_bargein.py` (FakeRoom, MockProvider with a long reply) asserts: a member mid-reply → exactly one interrupt with a plausible played_ms; a stranger or bleed copy → none; and in isolation a non-owner member → none. `make check` green.
  depends: T-043, T-041

- [ ] T-048 | M3 | Add CLI for sessions, join tokens, and the room agent
  goal: Expose `voiceroom session create|invite|accept|revoke|list`, `voiceroom token --session S --user U`, and `voiceroom room --session S --transport livekit|fake --provider openai|mock`.
  accept: Subcommands use the stores from T-046. `room` prints one line per turn (source, claimed identity, decision, label, latency) with no audio, embeddings, tokens or invite codes. `--debug` reuses the T-036 rule logging. `scripts/smoke/smoke_cli_room.py` runs via subprocess with `--embedder toy --transport fake --provider mock`: enroll ×3 → session create (collaboration) → invite/accept bob → room on a scripted 3-participant FakeRoom (asserts alice and bob committed, carol dropped/Unknown) → revoke bob → bob dropped. If the LiveKit binary and extra are present, it also runs `room --transport livekit` for one short scripted conversation. `make check` green.
  depends: T-045, T-046, T-047

- [ ] T-049 | M3 | Measure end-to-end room latency through the local LiveKit server
  goal: Report end-of-speech → commit and commit → first published reply audio through the real LiveKit transport, so the 700 ms target is checked with WebRTC jitter buffering in the path.
  accept: `python -m voiceroom.bench.room --transport livekit` (part of `make bench` when the binary and extra are present) streams ≥ 5 LibriSpeech multi-track conversations through the local server in realtime. It writes a "LiveKit transport" section in `reports/room-wespeaker-resnet34.md` with commit latency p50/p95 versus FakeRoom, transport-added delay, and the reply round trip with MockProvider. If p95 ≥ 700 ms, it adds a follow-up task naming the dominant stage. The key numbers go in summary.md. `make check` green.
  depends: T-042, T-045

- [ ] T-050 | M3 | Write multi-device room demo checklist and request human live test
  goal: Have the human verify a real multi-mic session (2–3 people on separate devices, plus one co-located pair for bleed), including the M1 items still unverified live: barge-in, collaboration attribution, and T-036 rule logging.
  accept: `docs/demo-room.md` and `scripts/live/run_room_demo.sh` give exact macOS steps: `uv sync --extra livekit`, `fetch_livekit.py` (or LiveKit Cloud), enroll, session create/invite/accept, `voiceroom room --transport livekit --provider openai --debug`, and how participants join (token via `voiceroom token`, and a browser client URL). They also include a script of who says what and the expected outcome per step (isolation, collaboration, impostor on a member's device, co-located bleed, barge-in, revoke) and a results form. `.work/tasks/T-050/needs_human.md` asks one question: local server on the Mac's LAN vs a LiveKit Cloud project (keys in `.env.agent`), with a recommendation. `make check` green.
  depends: T-048, T-049
  human: Choose LiveKit hosting (local LAN or Cloud keys), then run the multi-device checklist with real people and an OpenAI key, and report the results.
- [ ] T-0xx | M3 | Room clock from capture time, not frame count
  goal: A source that stops sending frames (muted) keeps its room clock behind; stamp frames with capture time so turn times and commit order stay right (needed by T-041 overlap/bleed test).
  accept: smoke with a source that pauses frames mid-run; its later turns get correct room times.
  depends: T-039
