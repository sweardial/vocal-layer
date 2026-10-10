# T-049 summary

## What changed
- `python -m voiceroom.bench.room --transport livekit` (new `bench/room_livekit.py`; the RTC publisher/listener code is in `adapters/livekit_harness.py` to keep the no-provider-imports-outside-adapters rule). It runs >= 5 bleed-variant LibriSpeech conversations (12 turns, 3 own-mic tracks + 1 shared mic) in realtime, one at a time, twice through RoomAgent + SileroVAD + MockProvider: over FakeRoom and over a local `livekit-server --dev`. It writes/replaces the "## LiveKit transport" section of `reports/room-wespeaker-resnet34.md` (other sections kept).
- Timing is wall clock from the publisher's last speech sample to `send_turn`, so jitter buffer/Opus/SFU are inside it. Transport-added delay = LiveKit minus FakeRoom, and per turn (commit latency - pipeline latency). Reply round trip: commit -> first `publish_audio` and commit -> onset heard by a listener participant.
- `make bench` runs it when `livekit` and the binary are present (skips otherwise). `make_work` extracted from `build` in `bench/room.py` (no behavior change).
- `FakeRoom(realtime=True)` now paces against a deadline (flat `sleep(dt)` drifted ~4 % slow and inflated FakeRoom latency).
- Smoke: `scripts/smoke/smoke_room_livekit_bench.py` (15 s, SKIPs without binary/extra).

## Results (final run, 5 conversations, 37 matched LiveKit commits)
- Commit latency p50/p95: FakeRoom 409/486 ms, LiveKit 512/558 ms -> **MEETS** 700 ms.
- Transport-added vs FakeRoom: p50 +103 ms, p95 +72 ms. Stage p95: endpoint 384 ms (fixed), compute 196 ms (queue wait p95 78 ms), transport (per turn) 147 ms.
- Reply round trip: commit -> published p50/p95 0/1 ms; commit -> heard by listener 51/57 ms (55 of 62 replies heard; the rest cut by barge-in/next turn).
- No follow-up task: p95 < 700 ms.

## Verification
- `make check` green (37 smoke scripts). Bench run: `uv run python -m voiceroom.bench.room --transport livekit` (~6 min).

## Notes / caveats
- Review round found that forcing `t_offset=0.0` skewed the per-track room clocks (SDK AudioStream emits silence from subscription) and added fake queue wait (first run: p95 740-771 ms "MISSES"). Fixed to use production offsets; the earlier miss was a bench artifact.
- Only ~60 % of commits are matched to a segment end (the rest are split turns/impostors); n is small (37). One conversation at a time: concurrent rooms in one asyncio loop starve the VAD. Localhost only: no real network, no TURN.
- A rare `livekit-server exited early (code 0)` flake was seen once at startup (free-port race).
- `smoke_livekit_room.py` (untouched by T-049) failed once in a full `make check` run and passed on 4 reruns; looks load/port-race flaky.
