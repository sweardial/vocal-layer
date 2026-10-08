# T-001 summary
Added `pyproject.toml` (uv, hatchling, py>=3.12, numpy/scipy/soundfile, ruff; dev: ruff, pytest; ruff excludes harness files `agentops/`, `cook`),
`src/voiceroom/__init__.py` (`__version__`), `scripts/smoke/run_all.py` (subprocess per `smoke_*.py`, PASS/FAIL/SKIP(exit 77) + timing, non-zero on fail or >60 s total),
`scripts/smoke/smoke_import.py`. Makefile unchanged. No unit tests (policy OFF). Independent reviewer subagent skipped (trivial task).

Verification:
- `uv sync && make check` -> green (lint passes, 1 smoke PASS 0.04s).
- Added `smoke_bad.py` exiting 1 -> `make smoke` exit 2 (non-zero); file then removed, `make check` green again.

Follow-ups: none.
