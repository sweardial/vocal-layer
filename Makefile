.PHONY: check lint smoke test bench
# T-001 completes this skeleton. Unit tests are gated by .work/POLICY.md (`make test` only runs once enabled).
check: lint smoke
lint:
	uv run ruff check . && uv run ruff format --check .
smoke:
	uv run python scripts/smoke/run_all.py
test:
	uv run pytest -q -m "not live"
bench:
	@echo "bench not implemented yet" && exit 1
