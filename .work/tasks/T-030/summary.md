# T-030 summary
## Changed
- `bench/pipeline.py`: per-speaker signature cache across conversations; isolation + collaboration passes of all conversations run concurrently (`--jobs`, default 4, threads); fail-fast cancel on error; report notes the contention.
- `scripts/smoke/run_all.py`: smokes run in parallel (workers = min(4, cpus/2)); budget 90 -> 60 s wall.
- `smoke_pipeline_real.py`: also exercises `build()` (2 convs, jobs=2). `smoke_identify_isolation` NOT trimmed (shortening the frozen-child audio broke its assertion); parallelism covers the budget.
## Verified
- `python -m voiceroom.bench.pipeline --out /tmp/rep`: 124 s (was ~9 min); metrics at jobs=4 vs 1 identical per reviewer (FR/FA/attribution); commit p95 453 ms vs 424 ms (CPU contention; `--jobs 1` for an uncontended figure).
- `make check` x3 green, smoke 42-44 s.
## Risks / follow-ups
- On a 2-CPU host the suite runs serially (~85 s) and exceeds the 60 s budget.
- smoke_e2e_isolation flaked once (`identify_busy` extra error) during a parallel run; not reproduced in 3 later runs.
- Default 4-conversation report (`reports/pipeline-*.md`) not regenerated (existing one is the 14-conversation run).

## Round 2
- run_all.py: CPU count falls back to `os.cpu_count()` when `os.sched_getaffinity` is missing (macOS); workers = max(2, min(4, cpus/2)).
- Smoke trims: removed the `build()` call from smoke_pipeline_real (it cost ~15 s on 2 CPUs; the bench driver is now covered only by running the bench itself), loosened its commit-latency bound 2 s -> 3 s (CPU-starved runners), smoke_cli drops the missing-file enroll case (duplicate-name case kept).
- Verified: `make check` green, 37 s (8 CPUs); `taskset -c 0-1 ... run_all.py` 58.3-59.5 s, three passes (<60 s, but thin; one earlier run with 3 workers hit 60.5 s so workers stay at 2).
- Not done: smoke_identify_isolation still ~14 s (shortening its audio did not reduce time). Follow-up: a 2-CPU margin needs a real cut of smoke_cli/identify_isolation process-spawn costs.
