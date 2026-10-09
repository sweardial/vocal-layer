# Review T-038: Extract SourceProcessor from Pipeline

Scope: `git diff HEAD` of `src/voiceroom/pipeline.py` and `scripts/smoke/smoke_windows_bench.py`; no untracked files. `make check`: green (26 scripts, 0 failed, 23.3 s).

I compared the old and new code paths for overflow, SpeechStart cancel, stream time, early-accept barge-in and cancellation, and they behave the same:
- Overflow: `_handle` clears `_early`/`_wins` before returning. `enqueue` calls `pt.cancel()`, which cancels exactly the tasks the old `cancel_all()` cancelled on QueueFull.
- SpeechStart: `close_open()` cancels the open turn's tasks and keeps `_stream_t`. It also zeroes `_early_len`, which the old code never read.
- Stream time: `reset()` zeroes it at the start of each run, as the old local did.
- Barge-in: `_on_early` now calls `policy.decide(final=False)` before checking `sink`. Both policies' `decide` are pure, so the result is the same.
- has_room ordering: `enqueue` now runs after window scoring in `push`. After a TurnEnd, `segmenter.active_frames` is 0, so no window is spawned in that gap. No change.
- Cancellation: the worker's `finally` still cancels `pt.wins`, and `run`'s `finally` still cancels the open turn through `src.close()`.

## Findings

[MEDIUM] src/voiceroom/pipeline.py:318: `Pipeline.policy` (and `.vad`/`.segmenter`) are public attributes, but decisions now go only through the `self.source.policy` copy made in `__init__`. Reassigning `pipeline.policy` after construction no longer has any effect. → `scripts/smoke/smoke_e2e_isolation.py:182` does `pl.policy = IsolationPolicy(..., **kw)` to test both the narrow-buffer early path and the "no-early fallback path with the policy's default (wide) buffers" (`kw={}`). Both loop iterations now run `make_pipe`'s narrow-buffer policy. The wide-buffer case is never run, yet the smoke still passes, so that check now passes without testing anything. Any caller that swaps the policy (or VAD/segmenter) on a live Pipeline would silently keep the old one. → Make `policy`/`vad`/`segmenter` properties on Pipeline that delegate to `self.source`, with setters that update the source (and call `bind_matcher`). Or change the smoke to build a new Pipeline with the policy instead of assigning it. Either way, check that the `{}` variant actually runs (e.g. `assert pl.source.policy is pl.policy`).

[MEDIUM] .work/tasks/T-038/ (missing summary.md and plan.md): the acceptance criteria require that `python -m voiceroom.bench.pipeline` on the same seed reproduce owner FR, non-owner FA, attribution and commit p95 within ±1 turn / ±30 ms of `reports/pipeline-wespeaker-resnet34.md`, with the numbers in summary.md. There is no summary.md and no bench output, and the task directory is empty (no plan.md either, although `require_plan_approval` is true). → With no bench numbers, a regression in real-model timing or decisions that the toy-embedder smokes don't exercise could merge unnoticed, e.g. the window-reuse path at TurnEnd under Silero/wespeaker latencies. The finding above shows the smokes have already lost some coverage. → Run the bench on the recorded seed and put the before/after numbers in summary.md.

[LOW] scripts/smoke/smoke_windows_bench.py:59,66: the acceptance criteria say "All existing smokes are unchanged". This smoke was edited because it called the private `Pipeline._continuity_ok` and `self._identify`. → The criterion is not literally met. A later refactor that renames `SourceProcessor._identify_fn` will break this smoke again in the same way. → Either keep a thin `Pipeline._continuity_ok` that delegates to `self.source`, or note the edit and why it was needed in summary.md.

Counts: HIGH 0, MEDIUM 2, LOW 1.
