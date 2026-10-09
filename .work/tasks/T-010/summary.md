# T-010 summary
- Added `voiceroom/bench/` (`metrics.py`, `run.py`, `report.py`, `__main__.py`): EER+threshold, FAR/FRR, threshold-for-FAR, length buckets, latency p50/p95, RTF; trials from conversations (every enrolled speaker is a claimed owner; target = same speaker); report `reports/bench-<embedder>.md` with dataset/seed/config header.
- Review fixes: added seeded 0.5/1/1.5 s cuts of each segment so short buckets are populated, an "all >=1 s" row, warm-up excluded from timing, default 12 conversations.
- Verified: `scripts/smoke/smoke_bench.py` (about 2.7 s), `make check` green.
- Follow-ups: `make bench` target not wired (T-012); default threshold = EER threshold on the same trials (choose on held-out data in T-012); too-few `--enroll-utts` gives a raw traceback.
