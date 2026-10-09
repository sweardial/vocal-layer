# Status

```
Voice Room · 2026-10-09 14:30
Phase: build · unit tests off · 32/33 done · est. API-equivalent cost $53.92
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
🔨 T-023 Write macOS demo checklist and request human live test
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
```

## Recent commits

```
7ad6c79 T-032: Cut sub-1 s interior-interjection leak without raising owner FR
92bc771 T-029: Calibrate pipeline continuity check and endpointing on real audio
86e4bbc T-031: Rework continuity check: longer windows / softer rule to cut owner false-reject
805c435 T-030: Speed up pipeline bench enrollment and trim smoke total
c13c64e wip(T-029): blocked, awaiting human
4b1105c T-028: Calibrate speaker-continuity window parameters on bench audio
a23e229 T-027: Ship AS-norm cohort and decide noise-aug enrollment defaults
63cdaff T-026: Evaluate CAM++ as an embedder candidate
2c81488 T-025: Reduce p95 embedding latency
6c4fef0 wip(T-023): blocked, awaiting human
```
