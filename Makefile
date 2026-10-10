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
	uv run $(BENCH_UV) python -m voiceroom.bench.pipeline
	uv run $(BENCH_UV) python -m voiceroom.bench.room
	@if uv run --no-sync python -c "import sys; import livekit.rtc; from voiceroom.adapters.livekit_server import find_binary; sys.exit(find_binary() is None)" 2>/dev/null; then \
		uv run $(BENCH_UV) --extra livekit python -m voiceroom.bench.room --transport livekit; \
	else echo "skip LiveKit room bench: livekit extra or livekit-server missing"; fi
