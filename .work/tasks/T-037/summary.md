# T-037 summary
Changes
- `scripts/smoke/run_all.py`: per-script table sorted by duration (slowest first); `-j N` option; children run with
  `OMP/OPENBLAS/MKL_NUM_THREADS=1` (root cause: concurrent smokes oversubscribed the CPUs with BLAS threads;
  e.g. smoke_cli used 26 s CPU in 10 s wall alone).
- `Makefile` unchanged: default workers (CPU-derived, 4 on 8 CPUs). Reviewer found `-j 6` flaky (smoke_e2e_isolation dropped a turn with identify_busy under contention), so it was not adopted; `-j` stays an opt-in override.
- No smoke was trimmed or edited, so no key assertion was lost (trimmed checks: none).

Verification (8 CPUs, real models/data present)
- Before: 40-59 s (T-030/T-036). `-j 8` without the thread cap: ~37 s and smoke_bench's `dt < 10` timing assert failed under contention.
- After (default workers): `make smoke` 23.18, 23.20, 23.02, 23.01, 23.09, 23.26 s over 6 runs, all passing; median ~23.1 s (<= 35 s). `make check` green.

Follow-ups: none. Note: slowest are still smoke_cli / smoke_identify_isolation (~10 s each).
Known: onnxruntime embedders set their own intra-op threads (up to 4) and ignore OMP_NUM_THREADS; not changed. agentops/config.json edit predates this task.

## Fix round 2
- `smoke_e2e_isolation.py`: the offline pipelines (early, late, `make_pipe` default, and the suffix-test pipelines) now pass `max_inflight_identify=1000`, like the existing `max_pending=1000`, so the faster-than-realtime source can't hit `identify_busy` drops under concurrent CPU load. Setup only; every assertion is kept (tests that set their own value, e.g. `max_inflight_identify=2`, still override it).
- Stability: `make smoke` (default workers) 12/12 passed, 22.9-23.4 s; `make check` green (23.06 s).
