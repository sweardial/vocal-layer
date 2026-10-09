# T-019 summary
## Changed
- `src/voiceroom/adapters/openai_realtime.py`: `OpenAIRealtimeProvider` (raw `websockets`, GA protocol, D-010). Key from env only, not in repr/logs (websockets handshake logger silenced). 16k→24k resample, 100 ms appends, attribution `input_text` item, response.create, 24 kHz output, cancel + truncate on interrupt.
- `scripts/smoke/fake_realtime_server.py` (fake server, rejects overlapping responses like the real API), `scripts/smoke/smoke_openai_adapter.py`, `scripts/live/openai_realtime_check.py` (manual).
- `pyproject.toml`/`uv.lock`: `websockets>=13,<16`. `.work/decisions.md`: D-010 appended (the planner clobbered the file; restored from git first).
## Review fixes (HIGH/MEDIUM)
New turn while a reply runs cancels it first; truncate sent even after `response.done`; queued cancelled audio purged; final chunk for every response end; turns encoded before sending, `input_audio_buffer.clear` on failure; reader survives bad events; reconnect closes old socket.
## Verification
`make check`: green (16 smoke scripts, openai adapter smoke ~2.5 s, stable over repeated runs). Not tested against the real API.
## Follow-ups
Live check of attribution quality; if weak, use single item with input_text+input_audio (D-010).

## Fix round 2
- Response tracking redone: each `response.create` carries an `event_id` and is tracked individually (FIFO `_pending` -> `_open`), with `error` events correlated by `event_id`. A superseding turn/interrupt marks everything cancelled and waits (<=5 s) until the server reports no pending/open response before sending the new request.
- All connection-scoped response/item state is reset on `close()` and `connect()`.
- Reply audio buffer is bounded (`MAX_BUFFER_S` = 30 s); on overflow the oldest audio is dropped, final markers and the end-of-stream sentinel kept.
- Fake server now sends `response.created` synchronously (matches ordering of the real API) and echoes `event_id` in errors.
- Smoke extended: 3 back-to-back sends without sleeps then a 4th (both replies have audio), reconnect right after a send, stalled consumer bounded buffer.
- Verification: `make check` green; adapter smoke passed repeatedly.

## Fix round 3
- Cancel timeout (`CANCEL_WAIT_S`) now fails the operation: `_abort()` drops the connection (state reset, audio stream ended) and `send_turn`/`interrupt` raise `ConnectionError`; the caller must `connect()` again. Deltas are accepted only for a known, non-cancelled response id.
- If `response.create` send fails or is cancelled (ambiguous), the connection is aborted the same way instead of leaving a stale pending request. Cancels are re-sent only when a new cancelled response appears or after 1 s, not on every unrelated server event.
- Reader task no longer pushes the end-of-stream sentinel after being superseded by `_abort`/`close` (it used to end a freshly reconnected stream).
- Buffer is bounded by entries too (`MAX_BUFFER_ENTRIES` = 256); overflow drops the oldest chunks and reply markers.
- Smoke extended (fake server gets `cancel_delay`): slow cancel ack -> ConnectionError and no late audio; interrupted `response.create` send -> teardown, reconnect works with no cancel storm; stalled consumer asserts the entry limit.
- Verification: `make check` green; adapter smoke passed repeatedly.
