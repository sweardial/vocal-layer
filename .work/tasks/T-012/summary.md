# T-012 summary
- Added `src/voiceroom/config.py` (default embedder wespeaker-resnet34, per-embedder thresholds 0.35 / 0.32, margin, enrollment params, MIN_DECISION_S). `Matcher(threshold=None)`, `enroll`, and `make bench` read it.
- D-009 in `.work/decisions.md`: clean ≥1 s FAR 1.18 % / FRR 0.28 % (targets met); noisy 10 dB ≥1 s FRR 16.9 % and p95 ~595 ms are gaps → follow-ups T-024..T-026.
- Verified: `make bench` (reports regenerated at configured thresholds), `make check` green. Reviewer found stale reports (fixed by the bench rerun), unreferenced config keys (documented as M1-consumed), D-009 numbers corrected.
- Not enforced yet: DEFAULT_EMBEDDER / MIN_DECISION_S (pipeline is M1).
