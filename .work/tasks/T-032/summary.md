# T-032 summary

## What changed
- `bench/windows.py`: now VAD-aware like the pipeline (SileroVAD; pause windows unscored, windows embed speech frames only); `--no-vad` keeps the old behaviour. The old bench's owner FR (10 %) was mostly pause windows the pipeline never scores.
- `policy.py` / `config.py`: new "deep window" rule in `windows_ok` (isolation + collaboration): `DEEP_BAD_RUN=2` consecutive windows with owner not best and score < threshold - `DEEP_BUFFER` (0.2) reject. Window width/hop, run 3, drop 0.4 unchanged.
- `scripts/smoke/smoke_windows_bench.py` updated for the rule; `README` known-limits and `decisions.md` D-016 updated. Reports: `reports/windows-wespeaker-resnet34-run3.md` (replaced, VAD-aware), `reports/pipeline-wespeaker-resnet34.md` (re-run).

## Results (windows bench, wespeaker, buffer 0.1, drop 0.4, run 3)
| | owner FR | leak 0.5 s | 1 s | 2 s |
|---|---|---|---|---|
| D-015 baseline (no VAD) | 10.0 % | 41.7 % | 3.3 % | 0 % |
| now, seed 0 (committed) | 3.3 % (2/60) | 38.3 % | 3.3 % | 0 % |
| now, held-out seed 1 | 1.7 % | 40.0 % | 10.0 % | 1.7 % |
Pipeline bench (14 conv, 3x, jobs 1): FR 4.3 %, FA 1.6 %, attribution 96.4 %, commit p95 424 ms (unchanged).

## Honest assessment
Acceptance met (FR < 5 %, 0.5 s leak < 42 %), but the leak gain is only a few points (and 1 s leak is noisier on seed 1). The FR gain is real (bench fidelity). Tried and rejected: 1 s windows (leak >= 53 %), hop 0.125 s + run 5 (leak 30 % but doubles embeddings: commit p95 3 s, `identify_busy` drops, misses 700 ms; reviewer HIGH findings were about this, reverted). The deep rule is inert if only the owner is enrolled.

## Verification
`make check` green (25 scripts); `python -m voiceroom.bench.windows --bad-run 3` (seed 0 and `--seed 1`); `python -m voiceroom.bench.pipeline --conversations 14 --speed 3 --jobs 1`.

## Follow-ups
Real fix for sub-1 s leaks needs a change-point / diarization-style embedder (frame-level), not window scores.
