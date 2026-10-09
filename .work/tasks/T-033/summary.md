# T-033 summary
- `sounddevice` added to main dependencies in pyproject.toml; uv.lock updated (v0.5.6).
- Removed `uv run --with sounddevice` workaround from docs/demo-macos.md and scripts/live/run_macos_demo.sh; README gets a "Live demo" pointer; io.py messages now say `uv sync`.
- Verified: `uv sync --extra ecapa && uv run python -c "import sounddevice"` OK; `make check` green (26 scripts, 0 failed).
- Not done: independent reviewer subagent skipped (trivial change). `--no-cohort` note remains (T-034).

## Fix round 2
- `smoke_e2e_isolation.py` was flaky under CPU load (unrelated to sounddevice): the offline source outruns real time, so some turns hit `identify_busy` before the expected `send_error` assertion. The failure-recovery case now uses `max_inflight_identify=1000`. `make check` green (26 scripts, 0 failed).
