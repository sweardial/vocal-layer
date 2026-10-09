# Status

```
Voice Room · 2026-10-08 21:43
Phase: build · unit tests off · 15/26 done · est. API-equivalent cost $13.64
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
⛔ T-016 Build streaming pipeline with isolation policy
     ❓ Couldn't converge after 3 fix rounds. How should I proceed? (e.g. 'skip', a hint, or a scope change)
⬜ T-017 Add collaboration mode with speaker attribution
⬜ T-018 Measure pipeline latency and isolation accuracy on real components
🔨 T-019 Implement OpenAI Realtime adapter against a local fake server
⬜ T-020 Add mic and speaker I/O with file-based stand-ins
⬜ T-021 Add voiceroom CLI for enroll, list, delete, and demo
⬜ T-022 Support barge-in: stop playback when accepted speaker talks
⬜ T-023 Write macOS demo checklist and request human live test
⬜ T-024 Improve noisy and short-utterance accuracy
⬜ T-025 Reduce p95 embedding latency
⬜ T-026 Evaluate CAM++ as an embedder candidate
```

## Recent commits

```
061da49 wip(T-016): blocked, did not converge
2d37406 T-015: Define provider adapter interface and MockProvider
0be7718 T-014: Implement segmenter with turn endpointing and early decision hooks
2c0366e T-013: Add streaming VAD with Silero ONNX and energy fallback
a9735c7 T-012: Choose embedder and threshold from bench results
3f0b28f T-011: Wire make bench to LibriSpeech for all embedders
5d2dc83 T-010: Implement bench metrics, trials, and report writer
ce62fbb T-009: Add optional SpeechBrain ECAPA embedder
fe3536a T-008: Build synthetic conversations with ground-truth segments
17fab74 T-007: Add LibriSpeech download script and corpus index
```
