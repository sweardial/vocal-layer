# T-048 summary
- `voiceroom session create|invite|accept|revoke|list`, `token --session --user [--url]`, `room --session --transport livekit|fake --provider openai|mock` in `src/voiceroom/cli.py`. Sessions live in `<store>/sessions` (override `--sessions`/`VOICEROOM_SESSIONS`) via the T-046 SessionStore; `room` uses SessionPolicy + SessionSync (revoked members kicked).
- `room` prints `turn N t= source= claimed= decision= label= latency=` only; `--debug` enables the `voiceroom.pipeline` rule logging (stderr). Fake transport needs `--script` JSON; tuning flags `--verify-threshold/--window-drop/--vad/--duration`.
- cmd_demo setup refactored into `_load_sigs/_scoring/_debug_logging`.
- Gotcha: LiveKitRoom must be built inside the event loop (built outside it hung connect).
- Verified: `scripts/smoke/smoke_cli_room.py` (fake flow incl. revoke; livekit part ran, binary present); `make check` green.
- Review: no HIGH; fixed token/timing flake and `--ttl-hours 0`/`--duration 0`. Not done: smoke doesn't force a T-036 rule line under `--debug` (code path unchanged, not asserted).

## Fix round 2
- `smoke_livekit_room.py` failed once in `make check` (passes alone, and in 5 later full runs; I never saw its actual assertion). Likely CPU contention from the heavy parallel `smoke_cli_room.py`, but the cause is unconfirmed. Mitigation: `smoke_cli_room.py` now sets OMP/MKL threads to 1 for its child processes and drops the redundant `--debug` room run. `--debug` stays unproven in the smoke.
- Verified: `make check` green; 3 consecutive `run_all.py` runs, 36 scripts, 0 failed (~44 s).
