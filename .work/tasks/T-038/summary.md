# T-038 summary
## Changed
- `pipeline.py`: new `SourceProcessor(source_id, vad, segmenter, matcher, policy)` (push/flush frames -> `PendingTurn`; `decide()` -> `DecidedTurn` with audio, label, decision, match, early flag, timings). Owns early/window scoring, continuity check, policy. Never touches the provider. Identify/has_room/early-accept hooks injected via `bind()`.
- `Pipeline.run` keeps its signature; it owns the identifier, queue, provider, barge-in, records. `Pipeline.vad/segmenter/policy` are pass-through properties to `self.source` (assignable, as before).
- `smoke_windows_bench.py`: only the private-method call moved to `SourceProcessor._continuity_ok` (stub renamed `_identify_fn`).
## Verification
- `make check`: lint ok, 26 smokes, 0 failed.
- `python -m voiceroom.bench.pipeline --conversations 14 --turns 6 --speed 3 --jobs 1 --seed 0` (out to /tmp, baseline report untouched) vs baseline: commit p95 425 vs 424 ms; decision p95 429 vs 430; owner FR 4.3% (1/23) same; non-owner FA 1.6% (1/61) same; attribution 80/84 vs 81/84 (-1 turn, within tolerance; timing-dependent).
- Review: no HIGH; MEDIUM (policy reassignment ignored) fixed via properties.
## Follow-ups
None.
