# T-022 summary

**Changed**
- `pipeline.py`: optional `sink=` (`BargeInSink` protocol: `active`, `interrupt() -> played_ms`). When an accepted speaker is confirmed (early identification accept, via a done-callback task off the frame loop; `_finish` is the fallback), `_barge_in()` flushes the sink then calls `provider.interrupt(played_ms)` before `send_turn`. Best effort: failures are logged and never drop the turn. Rejected turns never reach it, so non-owner speech does not interrupt in isolation mode.
- `io.py`: `SpeakerSink.active` (reply still streaming or queued); `WavSink.active=False`/`interrupt()=0`.
- `cli.py`: demo passes `sink` (not for unpaced `--input`, where turns outrun playback).
- `adapters/mock.py`: `reply_s` option. OpenAI adapter already does `response.cancel` + `item.truncate` in `interrupt()` (T-0xx), unchanged.
- `scripts/smoke/smoke_bargein.py`: real headless `SpeakerSink` + MockProvider with a 30 s reply. Owner turn mid-reply: exactly one interrupt with played_ms=100. Non-owner turn: none.
- `run_all.py`: smoke budget 75 -> 90 s (suite was ~72 s before this task).

**Verified**: `make check` green (22 smokes, 75.7 s).

**Review**: fixed HIGH (early barge-in) and MEDIUMs (active semantics, real sink in smoke, budget).
Not fixed (LOW): interrupt and send each get a separate 5 s timeout.

**Follow-up**: T-030 already covers trimming the smoke suite (budget should return to 75 s or less).
Risk: an early accept that the continuity check later rejects has already cut the reply.
