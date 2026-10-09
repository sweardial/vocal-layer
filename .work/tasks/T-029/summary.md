# T-029 summary
- `policy.windows_ok` (both policies) rejects only after `window_bad_run` consecutive bad windows (pauses break runs; capped for short turns). Config: wespeaker run 3, drop 0.4; ecapa unchanged (strict). Segmenter end_silence unchanged (latency). See D-015.
- `bench.windows --bad-run N`; `reports/windows-wespeaker-resnet34-run3.md`. Pipeline bench header now records window_drop/bad_run.
- `smoke_pipeline_real` uses config defaults; `smoke_windows_bench` checks the run logic.
- Verified: `python -m voiceroom.bench.pipeline --speed 3 --conversations 14` -> owner FR 4.3 % (1/23), FA 1.6 % (1/61), attribution 96.4 %, commit p95 433 ms. `make check` green.
- Risks: acceptance is marginal on a small sample; offline window bench FR 8.3 %. 0.5 s interjections mostly leak. Review findings addressed (run-4 -> 3, pause/short-turn handling, validation, ecapa reverted); smoke total time >60 s is T-030.

## Round 2
- Added foreign-enrolled-speaker guard, all-scored-bad rule across pauses, standalone-safe run fallback (Collaboration Unknown branch); smoke covers all three (`smoke_windows_bench`).
- Windows bench (`reports/windows-wespeaker-resnet34-run3.md`): FR 10 %, leak 0.5 s 42 %, 1 s 3.3 %, 2 s 0 %. Pipeline bench re-run: FR 4.3 % (1/23), FA 1.6 %, attribution 96.4 %.
- Short-interjection leak cannot be eliminated with 0.5 s windows; trade-off written to `needs_human.md` (recommendation: keep + T-031).
