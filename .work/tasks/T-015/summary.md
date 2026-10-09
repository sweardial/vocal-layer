# T-015 summary
- Added `voiceroom/adapters/` (`base.py`: `Provider` Protocol + `AudioOut`; `mock.py`: `MockProvider` recording turns/labels/monotonic timestamps/interrupts, 3-chunk synthetic tone reply per turn; interrupt drops unread reply chunks; `close()` ends `audio_out()`).
- `scripts/smoke/smoke_mock_provider.py`: 3 turns (order, labels, lengths, reply audio), interrupt, close, and a grep-style check for provider-specific imports outside `adapters/`.
- Verified: `make check` green (13 scripts, 0 failed).
- Follow-ups: none.
- Review fixes: `interrupt(played_ms)`, `AudioOut.reply_id`, mock close/interrupt/reconnect/single-consumer handling, input validation, AST import check. Re-ran `make check`: green.
