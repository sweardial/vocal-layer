# Review T-039: multi-source room pipeline (room.py, smoke_room_mux.py)

`make check`: green (27 scripts, 24.1 s). `smoke_room_mux.py` takes 3.5 s, under the 5 s limit.
No plan.md exists in `.work/tasks/T-039/`, but `agentops/config.json` has `require_plan_approval: true`.

## Findings

[MEDIUM] src/voiceroom/room.py:207-221 (with :194): End-of-speech order is only enforced among turns already in `_entries`. A turn whose speech ended earlier but whose `TurnEnd` has not yet been emitted can be overtaken. Normal endings emit `TurnEnd` one segmenter hangover (about 0.38 s) after `t_end`, but `remove_source` flushes and `max_turn_s` splits emit with no hangover.
→ Reproduced with real-time paced frames (`/tmp/t039/order.py`). Source `a`'s speech ends at 2.30 s. Source `b` is removed mid-turn at 2.45 s. Records come out as `[('b', 2.46), ('a', 2.3)]`, so the provider gets b's turn first. A split turn on a long talker overtakes another source's just-ended turn the same way, as does any per-source delivery jitter. The smoke cannot catch this because it streams about 12 s of audio in under 1 s, so every turn is already queued before the first commit.
→ Fix: add a watermark. Commit entry `e` only when every live source's room clock is at least `e.t_end + end_silence` (hangover), with a small cap such as 0.5 s so a stalled source cannot block commits. Also pace at least the cut/split region of the smoke at real time, and assert the order there.

[MEDIUM] src/voiceroom/room.py:141,159,194: Room time is `offset + frames_received * 512/16000`. It assumes every source delivers frames continuously from the moment it is added.
→ Suppose a track's frames stop for a while: the participant is muted, the transport sends nothing, or the network drops audio. That source's clock then lags wall time by the length of the gap, for good. After a 10 s mute, its next turn gets a `t_end` about 10 s too early. It sorts ahead of turns from other sources that really happened before it. Its `RoomTurnRecord.t_start`/`t_end` are wrong, and the time-overlap test in T-041's bleed detector, which relies on cross-source alignment, fails. `add_source(t_offset=None)` also inherits the max of these drifting clocks.
→ Fix: let sources supply per-frame timestamps, or re-anchor each source to a shared monotonic room clock (wall time since `run()` started) when frames arrive. A gap then advances `_stream_t`/`offset` instead of being absorbed. Document the contract for T-043.

[LOW] src/voiceroom/room.py:159 (with :151,:174): `_room_now()` only looks at the sources still present. Once every source has left, it returns 0.0, so a source added later gets `offset=0` and the room clock runs backwards.
→ In a 1:1 session where the only participant leaves at room t=100 and rejoins, the new source's turns have t ≈ 1 s. Records stop being monotonic. If the old source's flushed turn is still being decided, the new turn sorts ahead of it.
→ Fix: keep a monotonic `self._room_t` that is updated whenever a source is popped (and on push). Use it as the floor in `_room_now()`.

[LOW] src/voiceroom/room.py:151-153: `remove_source` raises `KeyError` when the source has already ended on its own. `_feed` pops it at :173-174 when its frame iterator finishes.
→ The T-043 RoomAgent maps `ParticipantLeft` to `remove_source`. When a participant unpublishes, their track iterator ends, the source is auto-removed, and the later `ParticipantLeft` event then raises `KeyError` in the agent's event handler.
→ Fix: make it idempotent, either returning `False` or no-op for unknown or finished ids, or document it and have the runner check `id in room.sources`.

[LOW] src/voiceroom/room.py:329-331: If `run()` is cancelled while it is inside `asyncio.wait`, `waiter.cancel()` is skipped. The `_wake.wait()` task stays pending forever. The source tasks, decide tasks and committer are cancelled in `finally` but never awaited.
→ When the RoomAgent shuts down by cancelling `run()`, it leaks a pending task, and asyncio logs "Task was destroyed but it is pending" at loop close. The same happens on every restart of a long-lived agent.
→ Fix: wrap the wait in `try/finally: waiter.cancel()`. At the end of `finally`, `await asyncio.gather(*cancelled_tasks, return_exceptions=True)`.

[LOW] src/voiceroom/room.py:320-321: `_running = True` is set, and the identifier is created, before `await self.provider.connect()`, which sits outside the `try`.
→ If `connect()` fails (for example with a network error), `_running` stays True and `_ident` is never closed, which leaks its thread pool. A later `add_source` then calls `_start` and creates a feed task with no committer. That source's turns are created and never committed. A retried `run()` then calls `_start` a second time on the same frames iterator ("asynchronous generator is already running").
→ Fix: connect before setting `_running` and creating `_ident`, or move both inside the `try` and reset `_running` in `finally`.

## Checked, no issue
- No `send_turn` calls interleave. `_commit` is awaited sequentially from the single committer. The smoke checks this independently with `SlowProvider.overlap` plus `max_concurrent_sends == 1`, so that check is not trivial.
- Commit order among queued turns is correct. `min(sort_key)` is re-checked after the decision wait, and ties are broken by `seq`.
- Flushing on `remove_source` works. The flushed `PendingTurn` keeps its copied early/window tasks, so `proc.close()` cancels only the open-turn state. The smoke really cuts b's turn (natural end 8.0 s, committed `t_end` 7.0).
- Logging is privacy-safe. Only the source id and fixed codes are logged, never audio or embeddings. No provider-specific code is used outside `adapters/`.
