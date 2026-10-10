# T-050 summary
- Added `docs/demo-room.md` (hosting choice, macOS setup, enroll, session/invite/token, `room --transport livekit --provider openai --debug`, browser join via meet.livekit.io Custom tab, 12-step script with expected outcomes incl. impostor, co-located bleed, barge-in, revoke, results form) and guided `scripts/live/run_room_demo.sh` (tokens/invite codes shown on screen only, never logged).
- `.work/tasks/T-050/needs_human.md`: local LAN vs LiveKit Cloud, recommends Cloud (LAN browsers need TLS for mic).
- `scripts/smoke/smoke_demo_room.py`: bash syntax + CLI commands/flags used exist.
- Review fixes: room agent PID bug (pipeline `$!` was `tee`), local-mode warning, impostor step mutes Bob, VOICEROOM_SESSIONS unset, headphone note, log check.
- Verified: `make check` green (38 scripts, ~53 s). The script itself is not run (needs macOS, mics, keys).
- Follow-up: `--debug` logs only continuity rejections, not 1:1 verify mismatches; not added.
