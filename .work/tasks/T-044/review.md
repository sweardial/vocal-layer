# T-044 review

Scope reviewed: `git diff HEAD` (pyproject `livekit` extra, uv.lock, D-018) + untracked `scripts/fetch_livekit.py`, `src/voiceroom/adapters/livekit_server.py`, `scripts/smoke/smoke_livekit_loopback.py`.

Verification done by reviewer:
- `make check`: green (ruff clean; 32 smokes, 0 failed, 32.9 s). `smoke_livekit_loopback.py` actually PASSed (3.3 s), it did not SKIP.
- Ran the loopback smoke 6x concurrently: all PASS (1.96-1.98 s received, peak 440 Hz, ~2.3 s each), no orphan `livekit-server` left behind (checked /proc).
- Lockfile only adds new packages (livekit, livekit-api, livekit-protocol, aiohttp stack, pyjwt, ...). No existing pins changed.
- The smoke's FFT check is non-trivial: if the SDK only relabelled 48 kHz frames as 16 kHz, the peak would land near 147 Hz and the check would fail.

No HIGH findings.

## Findings

[MEDIUM] src/voiceroom/adapters/livekit_server.py:57-63: the config body doesn't set `rtc.stun_servers`. The binary embeds the defaults `stun.l.google.com:19302` / `stun1.l.google.com:19302`, and when no STUN servers are configured the LiveKit server hands those defaults to joining clients as ICE servers. → On any machine with internet access (a dev laptop, or CI with egress), both rtc clients in `make check` do DNS lookups and send UDP STUN bindings to Google. That breaks the CLAUDE.md rule "Checks never touch the network". The container run was offline, so D-018's "no TURN, offline" verification never exercised this. → Pin a loopback-only STUN entry (e.g. `stun_servers: ["127.0.0.1:<udp_port>"]`) so the default list is not used. Confirm with a packet capture or strace (`connect`/`sendto` to non-127.0.0.1) on a networked host, and note the result in D-018.

[MEDIUM] .work/tasks/T-044/ (missing summary.md): the acceptance says the smoke "is run in the container after fetching, with the result in summary.md". The task directory is empty (no plan.md, no summary.md). → The task can be marked done with no recorded proof of the required run. Today the result only exists inside D-018 prose. → Write summary.md with the fetch output, the exact PASS line, and the platform/versions.

[LOW] src/voiceroom/adapters/livekit_server.py:73: the readiness probe uses `urllib.request.urlopen`, which honors `HTTP_PROXY`/`http_proxy`. Loopback is only bypassed when `NO_PROXY` lists it. → On a host with a proxy configured but without `no_proxy=127.0.0.1`, the probe goes to the proxy, which fails or returns 502 (an `HTTPError`, which is an `OSError`). The loop spins until the 8 s `TimeoutError`, the smoke FAILs, and the probe also hits the network. → Build an opener with `urllib.request.ProxyHandler({})`, or probe with a plain `socket.create_connection(("127.0.0.1", port))`.

[LOW] src/voiceroom/adapters/livekit_server.py:66,80-82: "always killed" only holds while the Python `finally` block runs. The child isn't in its own process group and has no parent-death signal. → If the smoke hangs (e.g. in an rtc call), `run_all.py`'s 60 s timeout SIGKILLs the Python process, the `finally` block never runs, and `livekit-server` is orphaned, holding ports and CPU. The same happens on SIGKILL or OOM of the parent. In T-045/T-048 long-running rooms this becomes a leaked server per crash. → On Linux, pass `preexec_fn` that sets `prctl(PR_SET_PDEATHSIG, SIGKILL)`, or use `start_new_session=True` and have callers or run_all kill the group. At minimum, document the limitation.

[LOW] src/voiceroom/adapters/livekit_server.py:37-45: `_free_ports` picks all three ports by binding TCP sockets and then closing them before the server starts. The `udp_port` is never checked for UDP availability, and all three have a close/rebind race. → If a concurrent run or another process grabs one of the ports in that window (or that UDP port is already bound), the server exits early and the caller gets `RuntimeError("exited early")`, a flaky failure. That gets more likely once T-045/T-048 smokes also start servers under `run_all`'s parallel workers. I could not reproduce it in 6 parallel runs. → Probe the UDP port with a `SOCK_DGRAM` socket, and retry startup once or twice on an early exit.

[LOW] scripts/fetch_livekit.py:35-37 (acceptance deviation): the acceptance requires a macOS arm64 binary, but the script is Linux-only and points macOS users to `brew install livekit`. That install is unpinned and unverified (not 1.13.9, no sha256). → On a Mac, the smoke runs against whatever server version brew provides, so the facts D-018 verified for 1.13.9 (config keys `rtc.tcp_port`/`udp_port`, `--config-body`, readiness on `/`) may not hold, and the smoke may FAIL or behave differently. → Have the human acknowledge the deviation (it's recorded in D-018). Optionally make `find_binary` or the smoke print the server version (`livekit-server --version`) and warn on a mismatch with the pinned VERSION.

[LOW] scripts/fetch_livekit.py:48-50: the "already present" fast path trusts the stamp file and never re-hashes `data/bin/livekit-server`. → If the binary is replaced or truncated (e.g. an interrupted manual copy, or a different build dropped in), the script still reports `ok ... 1.13.9` and the smoke runs an unverified binary. → Store the binary's own sha256 in the stamp and compare it on the fast path (about 50 MB, cheap).

## Notes (no action required)
- `agentops/config.json` adding `M3` to milestones is outside the task's stated scope. Presumably the harness or human made it. Confirm it is not the implementer's change.
- The smoke uses the dev key/secret only against a localhost server. No credentials, audio or embeddings are logged. Server stdout/stderr goes to DEVNULL.
- Provider-specific (LiveKit) code in `src/` is confined to `adapters/`.
