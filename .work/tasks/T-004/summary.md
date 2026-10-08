# T-004 summary
- Added `voiceroom/enroll.py` (`enroll`, `Signature` with embedding-free repr, `EnrollmentError`; 3 s windows / 1.5 s hop; energy+spectral-flatness speech gate, rejects < 10 s speech, placeholder until the real VAD).
- Added `voiceroom/matcher.py` (`Matcher(signatures, threshold, embedder=None, min_margin=0)`, `identify(audio)`, `identify_embedding(emb)`; `Match(name|None, score, margin, best, scores)`).
- Smoke `scripts/smoke/smoke_enroll_match.py`: accuracy 100% (30 utterances), unenrolled rejected 100%; also checks short audio, silence, noise, shape errors.
- Verified: `make check` green.
- Review fixes (3 MEDIUM): noise no longer counts as speech; Signature uses identity eq/hash; audio vs embedding split into two methods.
- Follow-ups: T-005 store must compare signatures by field, not `==`. Replace speech gate with Silero VAD once available. Threshold is calibrated in the smoke script only; real calibration is T-012.
