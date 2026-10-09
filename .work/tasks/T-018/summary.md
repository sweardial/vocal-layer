# T-018 summary

## Changed
- `src/voiceroom/bench/pipeline.py` (`python -m voiceroom.bench.pipeline`): streams N LibriSpeech conversations (3 enrolled speakers + 1 stranger; first enrolled = owner) at realtime pacing through SileroVAD + wespeaker + Pipeline + MockProvider, twice (isolation pass, collaboration pass with unknown="label"). Writes `reports/pipeline-<embedder>.md`: commit-latency p50/p95 vs 700 ms, owner FR by length bucket, non-owner FA, attribution accuracy. Flags: `--conversations --turns --speed --window-drop --embedder`.
- `scripts/smoke/smoke_pipeline_real.py`: 1 short (2-turn) conversation, ~7 s, SKIPs (exit 77) without assets. Uses `window_drop=0.3` (see finding below).
- `Makefile` bench also runs the pipeline bench. `run_all.py` BUDGET_S 60 -> 75 (was already ~59 s; the new real-component smoke pushes it to ~66 s).

## Results (`reports/pipeline-wespeaker-resnet34.md`, 4 convs, 24 segments, realtime, default config)
- Latency end-of-speech -> commit/drop, all turns (n=39): p50 384 ms, p95 494 ms: meets 700 ms. Only 1 turn was committed (p50 484 ms, n=1), so the commit-only latency is not statistically supported; the headline uses all turns (same work either way). Endpointing hangover is 384 ms of it; compute after TurnEnd p95 110 ms.
- Non-owner FA 0 % (0/17).
- **Owner FR 85.7 % (6/7); attribution 25 % (enrolled 6 %, stranger->Unknown 62 %).** This target is NOT met.
- Cause (diagnosed, not fixed): the provisional continuity check (`WINDOW_DROPS` 0.05, D-009 notes it was not calibrated on real audio) rejects real owner turns whose 0.5 s windows vary 0.31-0.51. Sweep at 4x pacing (same convs): window_drop 0.3 -> owner FR 42.9 %, attribution 54 %; window_drop 1.0 -> FR 42.9 %, attribution 62.5 %. The remainder is probably `window_buffer` and read-speech pauses splitting 24 segments into 39 turns (fragments are short). Follow-up T-029.
- Caveats: only 7 owner segments (small N, wide error bars); thread identification, not the production process isolation; clean speech.

## Verified
- `uv run python scripts/smoke/smoke_pipeline_real.py` PASS 7 s; `make check` green (19 smokes, ~66 s).
- `python -m voiceroom.bench.pipeline` ran in 539 s.

## Follow-ups
T-029 (calibrate continuity/endpointing), T-030 (bench/smoke speed).

## Review
Reviewer findings fixed: latency verdict now over all turns with n shown; FA/FR use a coverage rule (>=0.3 s or half); attribution picks max-overlap turn; Makefile uses $(BENCH_UV). Unfixed: (BUDGET_S issue resolved in round 2.)

## Fix round 2
- Non-owner FA now counts any positive overlap with the audio sent (turn span with 0.2 s pre-roll / 0.1 s tail); the >=0.3 s coverage rule stays for owner FR only. `smoke_pipeline_real.py` asserts a 200 ms partial leak is counted.
- Latency verdict now comes from committed turns only, with a min n=20: the report says **INSUFFICIENT DATA (n=1 commit)**; the commit p95 (489 ms, n=1) is NOT evidence the 700 ms target is met. Decision (commit-or-drop) latency is reported separately (n=39, p50 384 ms, p95 493 ms). Re-run after T-029 for a real commit-latency verdict.
- BUDGET_S restored to 60 (run_all.py unchanged vs HEAD). Trimmed workloads: openai adapter stalled-consumer sleeps 1.0->0.4 s, bench smoke 4->2 convs, e2e isolation 3->2 seeds, e2e collab 2->1 seed, pipeline smoke at 6x pacing. `make check` smoke total ~58.7 s (3 runs: 58.7-59.0 s; margin is thin; T-030 should cut more).
- Tried and rejected: parallel smoke runner (timing-sensitive smokes flake), shorter identify_timeout / single-turn frozen case (flake).
- Report regenerated (538 s). Numbers otherwise unchanged: owner FR 85.7 % (6/7), FA 0/17, attribution 25 %.
