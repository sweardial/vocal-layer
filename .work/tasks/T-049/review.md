# T-049 review

Scope: `git diff HEAD` + untracked (`room_livekit.py`, `livekit_harness.py`, `smoke_room_livekit_bench.py`). `make check`: green (37 scripts, 51 s; `smoke_room_livekit_bench.py` PASS in 15 s). There is no `plan.md` or `summary.md` in `.work/tasks/T-049/`.

Empirical probe (smoke-sized conversation, toy embedder, EnergyVAD, local livekit-server). For each source, the wall time of its first frame at the bot, relative to the publisher start `t0`:
- FakeRoom: all 0.000 s.
- LiveKit: -0.663, -0.653, -0.510, -0.510 s. The SDK `AudioStream` emits silence from the moment of subscription, and subscriptions are spread by about 150 ms.
- Commits on the two early-subscribed tracks waited 0.134 to 0.140 s in the queue. Commits on the late tracks waited 0.000 s. FakeRoom waits were about 0.

## Findings

[HIGH] src/voiceroom/bench/room_livekit.py:113: `add_source` is forced to `t_offset=0.0`, so each LiveKit source's room clock starts at its own first frame. These first frames are subscription-time silence, not the publisher start. So the room clocks of the 4 tracks are skewed by the spread in subscription times (about 150 ms in the probe). `RoomPipeline._behind` (room.py:328-336) compares those clocks. A commit on an early-subscribed track therefore waits in `_others_caught_up` until the later-subscribed tracks "catch up" by the skew.
→ Failure: the bench reports queue wait that comes from its own setup. Production (`runner.py:129`, `t_offset=None` → `_room_now()`) aligns late-added sources to the running clock, so it would not pay this wait. The report shows queue wait p50 40 ms / p95 211 ms, compute p95 262 ms and LiveKit commit p95 740 ms → **MISSES**. Most of the ~210 ms queue wait at p95 is this skew. Removing it would likely put p95 well under 700 ms. So the verdict, the "LiveKit minus FakeRoom +217 ms p95" figure and the follow-up T-052 (whose hypothesis is "jitter-buffered tracks lag in bursts") all rest on a bench artifact. FakeRoom has no skew (all tracks start at 0), so the comparison is also biased.
→ Fix: do not override `t_offset`. Let the pipeline use its production default. Read `pipeline.sources[sid].offset` and map room time to publisher time as `t - offset + (first - t0)`. Alternatively, keep `t_offset` but drop pre-`t0` frames in `tap()`. Then rerun, regenerate the section, and rewrite or retract T-052.

[MEDIUM] scripts/smoke/smoke_room_livekit_bench.py:49-56: the smoke only checks commit counts and `np.isfinite(lm.e2e).all()`. The isfinite check passes trivially on an empty list.
→ Failure: a regression in the stream→publisher shift or in segment matching (e.g. sign error in `shift`, a broken `tid_of` mapping) would leave every commit `unmatched`, empty all latency rows, and still pass. The bug above (a queue-wait skew on LiveKit only) would also pass.
→ Fix: assert `len(lm.e2e) >= 1` and `len(fm.e2e) >= 1`, and that every e2e value is in a sane range (e.g. 0 to 2 s). After the fix above, also assert that LiveKit `max(lm.wait)` stays small (e.g. < 0.1 s on the smoke conversation).

[LOW] src/voiceroom/bench/room_livekit.py:31,178-188: commits are kept only if their VAD end is within 0.5 s of the segment end. In the report, 25 of the 60 LiveKit commits (FakeRoom: 24) are excluded. The section says these are "split inside a segment, impostor", but that is asserted, not measured.
→ Failure: if WebRTC jitter or VAD trailing noise pushes the end past 0.5 s on slow turns, those turns are dropped, and the dropped turns are the ones that would raise p95.
→ Fix: count the exclusion reasons (no own/shared segment, coverage fail, end tolerance fail) and print them.

[LOW] Makefile:16: the probe runs `uv run ... --extra livekit python -c ...`. When the extra is missing, this does not detect absence: uv tries to install the extra, which touches the network, before falling back to "skip".
→ Failure: `make bench` on a machine without the extra silently downloads and installs `livekit`, or fails with hidden stderr and skips.
→ Fix: probe with plain `uv run python -c "import livekit.rtc"` (no `--extra`), or add `--offline`/`--frozen`.

[LOW] agentops/config.json: adds milestone `M3` to the harness config. This is outside the scope of T-049.
→ Failure: it widens the set of tasks the unattended harness will run, without being part of the reviewed task.
→ Fix: confirm the human made this change, or revert it from this diff.

[LOW] .work/tasks/T-049/: there is no `summary.md`, but acceptance requires "the key numbers go in summary.md". There is also no `plan.md`.
→ Fix: write the summary after rerunning with the HIGH fix.

Checked and OK:
- `make_work` extraction is behavior-preserving, and the `bench.room` default path is unchanged.
- FakeRoom deadline pacing only affects `realtime=True` users (the CLI `--realtime` and this bench). Burst catch-up after a consumer stall is bounded by `QUEUE_DEPTH`.
- `committed` records and `provider.turns` stay aligned (each record is emitted right after `send_turn`).
- Reply ids map 1-based per MockProvider.
- `turn.t` and `t0` both use `time.monotonic()`.
- Onset detection gives about 45 to 50 ms in the probe, which is plausible.
- No audio or embeddings are logged.
- The provider SDK is only imported in `adapters/`.
