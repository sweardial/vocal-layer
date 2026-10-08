# Status

```
Voice Room · 2026-10-08 19:15
Phase: build · unit tests off · 8/23 done · est. API-equivalent cost $3.90
✅ T-001 Set up project skeleton and smoke runner
✅ T-002 Add audio utilities and synthetic speaker generator
✅ T-003 Define pluggable embedder interface with toy embedder
✅ T-004 Implement enrollment signatures and cosine matcher
✅ T-005 Add encrypted file-based signature store with delete
✅ T-006 Add model fetch script and WeSpeaker ONNX embedder
✅ T-007 Add LibriSpeech download script and corpus index
✅ T-008 Build synthetic conversations with ground-truth segments
🔨 T-009 Add optional SpeechBrain ECAPA embedder
⬜ T-010 Implement bench metrics, trials, and report writer
⬜ T-011 Wire make bench to LibriSpeech for all embedders
⬜ T-012 Choose embedder and threshold from bench results
⬜ T-013 Add streaming VAD with Silero ONNX and energy fallback
⬜ T-014 Implement segmenter with turn endpointing and early decision hooks
⬜ T-015 Define provider adapter interface and MockProvider
⬜ T-016 Build streaming pipeline with isolation policy
⬜ T-017 Add collaboration mode with speaker attribution
⬜ T-018 Measure pipeline latency and isolation accuracy on real components
⬜ T-019 Implement OpenAI Realtime adapter against a local fake server
⬜ T-020 Add mic and speaker I/O with file-based stand-ins
⬜ T-021 Add voiceroom CLI for enroll, list, delete, and demo
⬜ T-022 Support barge-in: stop playback when accepted speaker talks
⬜ T-023 Write macOS demo checklist and request human live test
```

## Recent commits

```
fe3536a T-008: Build synthetic conversations with ground-truth segments
17fab74 T-007: Add LibriSpeech download script and corpus index
e4fa947 docs: HANDOFF.md for interactive sessions; point to it from CLAUDE.md
6d86fcc T-006: Add model fetch script and WeSpeaker ONNX embedder
c6253e8 harness: conversational Telegram, resumable sessions, self-reload, keep harness out of task commits
d1fb7a5 T-005: Add encrypted file-based signature store with delete
099463b Commit 1
d8e14d9 T-004: Implement enrollment signatures and cosine matcher
9c78516 T-003: Define pluggable embedder interface with toy embedder
d949408 T-002: Add audio utilities and synthetic speaker generator
```
