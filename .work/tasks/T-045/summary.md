# T-045 summary

## Changed
- `src/voiceroom/adapters/livekit_room.py`: `LiveKitRoom(url, token)` implements `RoomTransport` on `livekit` rtc.
  - Joins with `auto_subscribe=False` and subscribes only to remote audio publications; its own identity is skipped.
  - Inbound audio uses `AudioStream(sample_rate=16000, num_channels=1)`, so the SDK resamples (proven in T-044/D-018) and no `StreamResampler` is needed there. Frames go into a bounded per-track queue that drops the oldest audio when full, so the SDK callback never blocks.
  - Emits `ParticipantJoined(sid, identity, attributes)`, `TrackAudio` and `ParticipantLeft`. `RoomAgent` already prefers the `voiceroom_user` attribute over the identity.
  - `publish_audio` publishes through `rtc.AudioSource(24000)` in 10 ms frames. Chunks at other rates go through `StreamResampler`, flushed on `final`.
  - `make_token()` and `LiveKitRoom.from_env(room)` read `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`. The dev key pair is used only for localhost URLs. The token is never logged and `repr` shows only the URL.
- `scripts/smoke/smoke_livekit_room.py`: local server, 2 publishers (one claims its identity through `voiceroom_user`), a bot (`RoomAgent` + `MockProvider` + `TrackPolicy` "verified", toy embedder) and a listener.

## Verified
- `make check`: 33 scripts, 0 failed, about 33 s. `smoke_livekit_room.py` PASS in about 6-8 s (budget 15 s): 4 turns labelled synth000/synth001/synth000/synth001, and the listener heard about 5.7 s of reply audio.
- Teardown prints the known `FfiHandle.__del__` AssertionError noise to stderr (D-018). It is harmless and the exit code is 0.

## Notes and follow-ups
- Policy-level smoke: with `IsolationPolicy`, the Opus-coded toy-embedder turns were mostly rejected by the window continuity rule, so the smoke uses `TrackPolicy`, the M3 policy.
- `RoomTransport` has no interrupt hook, so barge-in does not clear audio already queued in the `AudioSource`. It could be added later with `AudioSource.clear_queue()`.
- Review round: fixed MEDIUM (dropped-audio warning, queue comment) and LOW (connect cleanup, resampler reset per reply). Not done: FFI teardown noise (LOW, D-018).
