# T-043 summary
Added `voiceroom/rooms/`: `base.py` (RoomTransport, ParticipantJoined/TrackAudio/ParticipantLeft, no LiveKit types),
`fake.py` (FakeRoom/FakeParticipant: scripted join/leave times, audio from arrays or wavs, records published audio),
`runner.py` (RoomAgent: join -> add_source with identity (or `voiceroom_user` attribute) as claimed_identity,
leave -> remove_source, provider reply audio pumped into `publish_audio`; wraps the provider so the pump starts after connect).
Smoke: `scripts/smoke/smoke_room_fake.py` (2 participants, late join, leave mid-turn, labels, non-empty published audio).
Verified: `make check` green (31 scripts, 0 failed).
Notes: room.py unchanged; RoomAgent replaces `room_pipeline.provider` with a wrapper. Barge-in/interrupt not wired (follow-up).
Review round 1 (see review.md): fixed HIGH (pipeline failure now ends RoomAgent.run) and MEDIUMs (ignored tracks drained, republished track replaces old, publish errors don't kill pump, FakeRoom emits Left before track end on mid-stream leave). make check green again.
