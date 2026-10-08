# Voice Room: a speaker-aware layer for realtime voice AI

## Problem

Realtime voice models (OpenAI Realtime, Gemini Live, …) assume a single speaker.
They answer anyone who talks into the microphone and have no idea who said what.
That makes them unusable when several people share a mic or a session.

## Product

An API/SDK that sits between audio sources and any realtime voice model provider.

- **Enrollment.** A user records ~20–30 s of speech and gets a *voice signature*
  (speaker embedding(s)). This requires explicit consent, is stored encrypted, and is deletable.
- **Isolation mode.** The model only hears and responds to the session owner.
  Other people's speech is dropped *before* it reaches the model.
- **Collaboration mode.** All enrolled participants are forwarded, and every turn is
  attributed so the model knows who said what. Unknown speakers follow a policy
  (drop, or label as "Unknown").
- **Sources.** A single shared mic (identity from voice), or multi-mic rooms like Google Meet
  (identity from the track, with voice used for verification). Multi-mic comes later.
- **Sessions & invites** (later). An owner invites another enrolled user into a session.

## Architecture

```
audio frames (16 kHz mono PCM)
  → VAD (e.g. Silero)
  → segmenter / turn endpointing
  → speaker embedder (pluggable)
  → matcher (cosine vs enrolled signatures, calibrated thresholds, early decision when confident)
  → policy (isolation | collaboration)
  → provider adapter (OpenAI Realtime first; MockProvider for tests)
  → model audio out
```

Key principles:
- **We own turn-taking.** The provider's server-side VAD / auto turn detection is
  disabled. We commit audio turns explicitly, and only for accepted speakers.
- **Attribution in collaboration mode.** Before each audio turn, inject a short text
  item (e.g. `[Speaker: Alice]`) so the model knows who is talking. The exact mechanism
  is per-provider and lives inside the adapter.
- **Provider-agnostic.** No provider-specific types leak outside `adapters/`.
- Components are small, separately testable, and work on CPU only (GPU optional).

## Non-functional targets (measure, report, iterate)

- Added latency from end-of-speech to commit: p95 < 700 ms on an Apple-silicon CPU.
- Isolation false-accept (non-owner speech forwarded): < 2 % on the eval set.
- Owner false-reject for utterances ≥ 1 s: < 5 %. Report short utterances (< 1 s) separately.
- Privacy: voice signatures are biometric data (GDPR Art. 9, Illinois BIPA). Store
  embeddings, not raw audio (unless the user explicitly opts in). Encrypt at rest.
  Provide deletion. Never log audio or embeddings.

## Milestones

### M0: Speaker-ID core + benchmark
- Python 3.12 package `voiceroom` (src layout), uv, ruff, `make check` (lint + smoke scripts).
- Library-level enrollment: audio → signature, saved in an encrypted file-based store with delete.
- Pluggable embedders. Evaluate at least two (e.g. SpeechBrain ECAPA-TDNN, WeSpeaker)
  and record the choice in `.work/decisions.md`.
- Eval data: LibriSpeech (openslr.org: dev-clean, or the mini subset), downloaded by a
  script into `data/` (gitignored). Build synthetic "conversations" that interleave
  utterances of 2–4 speakers with pauses, plus optional noise. Include ground-truth segments.
- `make bench` writes `reports/bench-<embedder>.md` with: EER, FAR/FRR at the chosen
  threshold, results bucketed by utterance length, per-segment latency p50/p95, and real-time factor.
- **Done when** the bench runs end to end from a clean checkout (after the data
  download), and the threshold is chosen and justified in `.work/decisions.md`.

### M1: Live isolation with a realtime model
- Streaming pipeline: frames → VAD → segmenter → identify → policy → adapter.
- Provider adapter interface, a `MockProvider` (records what was committed), and an
  OpenAI Realtime adapter. **Check the current official API docs**: use manual turn
  handling, send audio only for accepted turns, and play the model's audio out.
- Isolation mode, plus basic collaboration mode with speaker-attributed turns.
- E2E smoke checks: synthetic conversations through the pipeline with `MockProvider`. Check
  that only owner turns are committed (isolation) and that attribution is correct
  (collaboration). No network access.
- CLI: `voiceroom enroll --name alice [--file x.wav]` and
  `voiceroom demo --mode isolation --owner alice` using the host mic and speaker (sounddevice).
- The container has no mic, so implement this and test it with mocks. Then request a human
  check via `needs_human.md` with exact commands to run on macOS.
- **Done when** the e2e smoke checks pass, pipeline latency is measured and reported, and the
  human demo checklist is written.

### Later (out of scope until planned)
- M2: second provider (Gemini Live) and unknown-speaker policies.
- M3: LiveKit rooms, invites, multi-mic sessions.
- M4: public API (FastAPI + WebSocket), auth, consent flows, client SDK.
- v2: overlapping speech on one mic (target-speaker extraction).

## Environment notes for agents
- You run inside a Linux Docker container: CPU only, **no mic or speaker**, network available.
- `OPENAI_API_KEY` may be present in the env. Never print or commit it. Anything calling
  real APIs lives in `scripts/live/` and is run manually, never by `make check`.
- Unit tests are deliberately deferred until the human enables them (`.work/POLICY.md`).
