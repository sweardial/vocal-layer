.PHONY: check lint smoke test bench
# ECAPA needs the extra; `make bench BENCH_UV=` runs wespeaker only (plain uv run drops torch).
BENCH_UV ?= --extra ecapa
# T-001 completes this skeleton. Unit tests are gated by .work/POLICY.md (`make test` only runs once enabled).
check: lint smoke
lint:
	uv run ruff check . && uv run ruff format --check .
smoke:
	uv run python scripts/smoke/run_all.py
test:
	uv run pytest -q -m "not live"
bench:
	uv run $(BENCH_UV) python -m voiceroom.bench --corpus librispeech --embedder all
