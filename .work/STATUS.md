# Status

```
Voice Room · 2026-10-09 21:31
Phase: build · unit tests off · 48/52 done · est. API-equivalent cost $83.21
⏳ Usage limit, resuming ~21:52
✅ T-001 Set up project skeleton and smoke runner
✅ T-002 Add audio utilities and synthetic speaker generator
✅ T-003 Define pluggable embedder interface with toy embedder
✅ T-004 Implement enrollment signatures and cosine matcher
✅ T-005 Add encrypted file-based signature store with delete
✅ T-006 Add model fetch script and WeSpeaker ONNX embedder
✅ T-007 Add LibriSpeech download script and corpus index
✅ T-008 Build synthetic conversations with ground-truth segments
✅ T-009 Add optional SpeechBrain ECAPA embedder
✅ T-010 Implement bench metrics, trials, and report writer
✅ T-011 Wire make bench to LibriSpeech for all embedders
✅ T-012 Choose embedder and threshold from bench results
✅ T-013 Add streaming VAD with Silero ONNX and energy fallback
✅ T-014 Implement segmenter with turn endpointing and early decision hooks
✅ T-015 Define provider adapter interface and MockProvider
✅ T-016 Build streaming pipeline with isolation policy
✅ T-017 Add collaboration mode with speaker attribution
✅ T-018 Measure pipeline latency and isolation accuracy on real components
✅ T-019 Implement OpenAI Realtime adapter against a local fake server
✅ T-020 Add mic and speaker I/O with file-based stand-ins
✅ T-021 Add voiceroom CLI for enroll, list, delete, and demo
✅ T-022 Support barge-in: stop playback when accepted speaker talks
✅ T-023 Write macOS demo checklist and request human live test
✅ T-024 Improve noisy and short-utterance accuracy
✅ T-025 Reduce p95 embedding latency
✅ T-026 Evaluate CAM++ as an embedder candidate
✅ T-027 Ship AS-norm cohort and decide noise-aug enrollment defaults
✅ T-028 Calibrate speaker-continuity window parameters on bench audio
✅ T-028 Collaboration: split turns on mid-turn speaker change instead of dropping
✅ T-029 Calibrate pipeline continuity check and endpointing on real audio
✅ T-030 Speed up pipeline bench enrollment and trim smoke total
✅ T-031 Rework continuity check: longer windows / softer rule to cut owner false-reject
✅ T-032 Cut sub-1 s interior-interjection leak without raising owner FR
✅ T-033 Add sounddevice as a dependency and fix demo docs
✅ T-034 Make the AS-norm cohort file portable across machines
✅ T-035 Reduce owner false-reject on real mic audio
✅ T-036 Log which continuity rule fired in the live demo
✅ T-037 Free smoke-time headroom before M3 adds room smokes
✅ T-038 Extract a per-source turn processor from Pipeline
✅ T-039 Build multi-source room pipeline with serialized commits
✅ T-040 Add track-identity verification policy (verified and shared tracks)
✅ T-041 Suppress cross-talk bleed between co-located microphones
✅ T-042 Benchmark room isolation and attribution on LibriSpeech multi-track audio
✅ T-043 Define room transport interface with in-memory FakeRoom
✅ T-044 Fetch a pinned local LiveKit server and prove an offline loopback
✅ T-045 Implement LiveKit room transport against the local server
✅ T-046 Add sessions and invites with membership-driven policy
✅ T-047 Support barge-in and reply playback in rooms
🔨 T-048 Add CLI for sessions, join tokens, and the room agent
⬜ T-049 Measure end-to-end room latency through the local LiveKit server
⬜ T-050 Write multi-device room demo checklist and request human live test
⬜ T-051 Add stranger-tail turns to the room bench and fix narrowband owner FR
```

## Recent commits

```
7010ee0 T-047: Support barge-in and reply playback in rooms
a156957 T-046: Add sessions and invites with membership-driven policy
5a2e5be T-045: Implement LiveKit room transport against the local server
2bbc12c T-044: Fetch a pinned local LiveKit server and prove an offline loopback
ad9ac31 T-043: Define room transport interface with in-memory FakeRoom
261fd09 T-042: Benchmark room isolation and attribution on LibriSpeech multi-track audio
dd6fee9 T-041: Suppress cross-talk bleed between co-located microphones
d7344fb T-040: Add track-identity verification policy (verified and shared tracks)
f4e012a T-039: Build multi-source room pipeline with serialized commits
3501304 T-038: Extract a per-source turn processor from Pipeline
```
