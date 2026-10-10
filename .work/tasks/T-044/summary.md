# T-044 summary
Added: `scripts/fetch_livekit.py` (livekit-server 1.13.9, sha256-pinned, linux amd64/arm64 -> data/bin/),
extra `livekit` (livekit==1.1.20, livekit-api==1.2.1), `adapters/livekit_server.py` (`run_dev_server()`:
free ports, loopback-only STUN, no-proxy readiness probe, always killed), `scripts/smoke/smoke_livekit_loopback.py`.
D-018 now records the verified facts.

Verified in the container (linux arm64): `uv run python scripts/fetch_livekit.py` OK; smoke prints
`PASS: 1.98s at 16000 Hz from {'alice'}, peak 440 Hz, 2.3s` (two Rooms in one process; AudioStream
resamples 48k->16k; no TURN). `make check` green (32 scripts, 0 failed).

Caveats: upstream ships no macOS binary, so macOS uses `brew install livekit` / LIVEKIT_SERVER_BIN (not pinned;
deviates from the acceptance text). Loopback-only STUN is set to avoid Google STUN; not packet-captured on a
networked host. Review LOWs not fixed: UDP port not probed, marker file trusted, smoke force-kill leaves server.
