# T-050: needs a human

**Question:** Which LiveKit hosting for the multi-device test: (A) a local `livekit-server` on the Mac's LAN, or (B) a LiveKit Cloud project (URL, API key and secret in `.env.agent`, plus `OPENAI_API_KEY`)?

- **A. Local LAN**: no account, but only the Mac's own browser can join without TLS. Other devices' browsers block mic access and a `ws://` connection from the hosted client page. Reaching them needs a TLS tunnel or a self-hosted client.
- **B. LiveKit Cloud** (free tier is enough): `wss://` works from any phone or laptop, with no network setup.

**Recommendation: B.** Put `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` in `.env.agent`, then run `bash scripts/live/run_room_demo.sh` (details: `docs/demo-room.md`) with 2–3 people, one pair sitting together, and paste the results form into `.work/tasks/T-050/answers.md`.
