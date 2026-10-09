# T-024 summary
- Added `bench/compare.py` (`python -m voiceroom.bench.compare`) comparing 8 variants of multi-centroid (top-3 chunks, 3 k-means), AS-norm and noise-augmented enrollment; report `reports/compare-wespeaker-resnet34.md`.
- Best: noise-aug + AS-norm: 10 dB ≥ 1 s FRR 18.2 % -> 12.4 % at FAR 0.97 % (target < 5 % NOT met). Recorded in D-011.
- Wired behind config, off by default: `ENROLL_NOISE_SNRS` (`enroll(noise_snrs=)`, new `augment.py`), `Matcher(cohort=)` + `ASNORM_THRESHOLDS` (new `scoring.py`).
- Review (0 HIGH, 5 MEDIUM) addressed: cohort validated/normalized/enrollment stats cached, smoke strengthened, pickle cache removed, scale caveats in config + D-011 + T-027. The flaky e2e smoke only failed under bench load; green when idle.
- Verified: `make bench` exit 0 (reports refreshed), `make check` green (17 smokes, lint). New smoke: `scripts/smoke/smoke_noise_robust.py`.
- Follow-up: T-027 (ship cohort file, rescale IsolationPolicy early buffers, set defaults).
- Re (inbox "what's the progress"): this is done; the 10 dB FRR target remains open.

## Fix round 2
- HIGH (degenerate cohort accepted strangers): `scoring.norm_stats` now raises on non-finite or std < 1e-3 (`MIN_NORM_STD`) instead of dividing by ~1e-6. `Matcher` rejects non-finite/zero cohort rows, fails at construction if the enrollment-side spread is degenerate (the duplicate-row repro now raises ValueError), and fails closed (name None, score -inf) if the probe-side spread is degenerate.
- MEDIUM (smoke): `smoke_noise_robust.py` calibrates a threshold on the synthetic cohort and asserts own/oth names are "a"/"b", two unenrolled strangers get `None`, and degenerate/bad cohorts are rejected.
- Verified: `make check` green (lint + 17 smokes).
