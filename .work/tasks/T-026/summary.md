# T-026 summary
- Added `campplus-en-voxceleb` (3D-Speaker CAM++ ONNX via sherpa-onnx, Apache-2.0, sha256-pinned in fetch_models.py): `embedders/campplus.py`, registry, `make bench` list (skipped if model not fetched, unless named), threshold 0.376 in config, `scripts/smoke/smoke_embedder_campplus.py`.
- Result (D-012): worse than wespeaker (clean EER 4.24 vs 3.33 %, 10 dB 11.25 vs 10.10 %), no noise gain, ~2x faster. Wespeaker stays default; D-009 unchanged. Report: `reports/bench-campplus-en-voxceleb.md`.
- Verified: `make check` green (CAM++ smoke ran, RTF 0.008). Full `make bench` NOT confirmed: the final run was killed at the 10 min background limit, so acceptance "make bench green" is unproven (CAM++ report itself is complete, from a direct bench run); reviewer: 0 HIGH/MEDIUM; LOW fixed (bench skips missing non-default models).
- Caveat: latency comparison from separate runs; smoke suite ~76 s (pre-existing, T-030).

## Fix round 2
- `make check` failed on the smoke time budget (90.8 s > 90 s; the new CAM++ smoke added ~1.7 s to a suite already near the limit). (Round-2 trim of `smoke_identify_isolation.py` was WRONG and is reverted in round 3.)
- Full `make bench` still unconfirmed (see above).

## Fix round 3
- My round-2 trim broke `smoke_identify_isolation.py`: the frozen matcher answers one turn, then freezes, so the second turn is what times out and gets the child killed. Reverted to the original two 12 s turns (file now identical to HEAD; passed 3/3 standalone).
- The 90.8 s budget failure in round 2 was load noise: the suite normally takes ~75 s (74.8 s now) and the same run in round 3 took 72 s. No budget or smoke change was needed.
- `make check` green (23 scripts, 0 failed, 74.78 s). Full `make bench` still unconfirmed. Suite headroom is thin (T-030).
