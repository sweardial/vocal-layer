# T-047 summary
- `RoomTransport` gained `audio_active()` / `interrupt_audio() -> played_ms`; `PlaybackClock` (rooms/base.py) tracks published/played audio and cancelled reply ids. Implemented in FakeRoom and LiveKitRoom (`AudioSource.clear_queue()`, in-flight chunk aborts, cancelled replies dropped).
- `RoomPipeline.barge_in` hook: fired on early accept (speaker confirmed mid-turn; skipped while another source is speaking, because of possible bleed) and again just before an accepted turn is sent (after bleed/gate checks). `RoomAgent._barge_in` flushes the room and calls `provider.interrupt(played_ms)` once (no-op if nothing is playing).
- The agent's own identity (`transport.self_identity`, FakeRoom) is never a source.
- Review fixes: early barge-in (HIGH), played_ms excludes provider pauses (MED), LiveKit interrupt proof in smoke_livekit_room.py (MED).
- Verified: `make check` green; `scripts/smoke/smoke_room_bargein.py` (member 1 interrupt within 1.5 s of speech onset, played_ms ≈ wall clock; bleed copy no extra; stranger/isolation non-owner/own-identity none).
- Follow-ups / limits: early barge-in skips `deliver_gate`; bot's reply picked up by another member's mic is not modelled (only a risk with `unknown="label"`).
