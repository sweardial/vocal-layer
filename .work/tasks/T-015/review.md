# T-015 review

Scope: `src/voiceroom/adapters/{__init__,base,mock}.py`, `scripts/smoke/smoke_mock_provider.py`.
`make check` is green (lint passes, 13 smoke scripts pass, smoke_mock_provider takes 0.30 s). There is no plan.md for this task.
I confirmed the edge cases below with a local probe script (not committed).

## Findings

[MEDIUM] src/voiceroom/adapters/base.py:28: `interrupt()` takes no playback position. → D-007 says barge-in is `response.cancel` + `conversation.item.truncate`, and truncate needs `audio_end_ms`, which is how much reply audio the user actually *heard*. Only the sink knows that. The adapter knows only what it received, and the provider often streams the reply faster than realtime. So the T-019 adapter cannot truncate correctly. The model's context keeps the full unheard reply, and on the next turn it answers as if the user heard it. Freezing this signature now means T-019/T-022 must change the interface again. → Change it to `interrupt(played_ms: int | None = None)`, documented as "ms of the current reply that was played out", and have MockProvider record the value.

[MEDIUM] src/voiceroom/adapters/base.py:10-16 (`AudioOut`): there is no reply/turn id on output chunks. → With a real provider, `response.output_audio.delta` events that were already in flight keep arriving after `response.cancel`. The consumer (SpeakerSink, T-022 barge-in) cannot tell stale chunks of the cancelled reply from the new reply's chunks, so it plays the cut-off reply again right after barge-in. The mock hides this because `interrupt()` synchronously drains its own queue. → Add `reply_id: int` (or `turn_index`) to `AudioOut`. Document that adapters must drop chunks of cancelled replies, or that consumers drop chunks whose id is below the last interrupted one. Have the mock tag its chunks.

[MEDIUM] src/voiceroom/adapters/mock.py:53-56: `interrupt()` drains everything, including the `None` close sentinel. → Shutdown path: `close()` then `interrupt()` (for example, a barge-in or stop-playback handler fires during teardown). The sentinel is discarded and `audio_out()` never ends. The pipeline/IO task awaiting it hangs, and `asyncio.run` never returns. Reproduced: connect → close → interrupt → iterating `audio_out()` hangs. → Drain only `AudioOut` items and re-put the sentinel if one was seen. Better: keep a `_closed` flag that `audio_out` checks, and make `interrupt()` a no-op after close.

[MEDIUM] src/voiceroom/adapters/mock.py:30,33,58-64: there is one shared queue and one sentinel, which is never reset. → (a) If two consumers call `audio_out()` (for example, a pipeline latency recorder plus SpeakerSink), chunks are split between them, and `close()` ends only one; the other hangs forever. Reproduced: 1 done, 1 pending. (b) After `close()` → `connect()` (a reconnect, or reuse across conversations in the T-018 bench), the stale `None` is still queued, so the new `audio_out()` ends immediately and that turn's reply is silently lost. Reproduced: it yields `[]`. → Make `connect()` create a fresh queue. Document in `base.py` that `audio_out()` is single-consumer, and raise if it is called twice. Or broadcast to per-iterator queues.

[LOW] src/voiceroom/adapters/mock.py:36-40: `send_turn` does no input validation. It accepts empty arrays, int16, and 2-D audio. → An empty (0-sample) or very short turn from the segmenter is recorded and answered by the mock, but the real OpenAI adapter's `input_audio_buffer.commit` rejects buffers under 100 ms. E2E smokes (T-016/T-017) pass on behaviour that fails live. int16 input is cast without scaling (a value of 16000 is recorded as 16000.0, far outside [-1, 1]). Reproduced both. → In `base.py`, document the contract (1-D float32, minimum length) and have the mock raise `ValueError` on empty/non-1-D/non-float input, so contract violations show up offline.

[LOW] src/voiceroom/adapters/mock.py:27,39-41: `self.turns` keeps every committed turn's full audio forever. → `voiceroom demo --provider mock` (T-021) on the mic for an hour holds about 230 MB of raw speech in memory, and keeps buffered user audio longer than the privacy posture needs. → Add a `keep_audio: bool = True` flag, or store only `len(audio)` plus a bounded `deque(maxlen=...)` of recent turns.

[LOW] scripts/smoke/smoke_mock_provider.py:14-16: the banned-import regex is narrow. → `import json, openai`, `import os; import openai` on one line, `importlib.import_module("openai")`, and other SDKs (`livekit`, `elevenlabs`, `aiohttp` WS clients) all pass the check. The check also passes trivially today, because no provider code exists yet. → Use `ast` to collect every `Import`/`ImportFrom` module name (top-level package), and compare against a list that includes the SDKs SPEC plans (openai, websockets, google.genai, livekit).

[LOW] scripts/smoke/smoke_mock_provider.py:35,43: two assertions can never fail. `first is not nxt` is always true because every reply creates new `AudioOut` objects, even if `interrupt()` were a no-op. `a.t <= b.t` on `time.monotonic()` is always true. → The interrupt is in fact covered by the final `== 2` count, but these two lines suggest more coverage than they give. → Assert that `nxt` is the first chunk of the new reply (`not nxt.final` and, once added, `nxt.reply_id != first.reply_id`). Drop the timestamp assert or replace it with a check that turn timestamps fall within the run's start/end.

## Not issues
- No secrets, no audio/embedding logging, and no unsafe deserialization.
- No provider-specific code outside `adapters/`.
- `audio_out` being an `async def` generator satisfies the Protocol's `def -> AsyncIterator`.
- The smoke check is offline and fast.
