# T-023 summary
- Added `docs/demo-macos.md` (prereqs, setup, enroll x2, isolation, collaboration, OpenAI check, headphones advice, report-back) and a results form in needs_human.md (now answered; partial result accepted by the human).
- Live result (macOS, arm64, AirPods, MacBook mic, phone-call second voice): setup, fetch_models, enroll x2 and OpenAI check OK (first audio 0.79 s). Isolation checked on real audio ONLY with `--no-cohort`: Bob forwarded 0 times; 3 of 8 alice turns identified but dropped (owner false-reject); ~420-450 ms per turn.
- UNVERIFIED on real audio: barge-in, collaboration/attribution, echo, and the AS-norm cohort path.
- Docs now note the sounddevice and cohort workarounds (`uv run --with sounddevice`, `--no-cohort`).
- Verified: `make check` green after the doc edit.
- Follow-ups added: T-033 (sounddevice dependency), T-034 (portable cohort file), T-035 (owner false-reject).

## Fix round 2
- `scripts/live/run_macos_demo.sh`: `.env` is now loaded before the demo store is chosen; the store is `/tmp/vr-demo-*` only, refused if it equals `VOICEROOM_STORE`, and every `voiceroom` subcommand gets an explicit `--store`. Cleanup removes only that demo store, so personal signatures are never deleted. Test overrides: `VOICEROOM_ENV_FILE`, `VOICEROOM_DEMO_OUT`, `VOICEROOM_DEMO_STORE`.
- `docs/demo-macos.md`: every mic command uses `uv run --with sounddevice` and `--store "$VOICEROOM_STORE"`; both demo blocks have `--no-cohort` (until T-033/T-034).
- New `scripts/smoke/smoke_demo_script.py`: runs the script with stubbed `uv`/`uname`/`sw_vers` and a `.env` pointing at a personal store; asserts all commands use the demo store and the personal store survives.
- Verified: `make check` green.

## Fix round 3
- `run_macos_demo.sh`: a new run creates a private `mktemp -d` store (`vr-demo.XXXXXX`, canonicalized) and records it in `$OUT/demo-store.txt`. `--demo-only` resumes only that recorded store, and only if it is a non-symlink, user-owned `vr-demo.*` dir with no `.`/`..` components. Otherwise it exits before running any command. Cleanup removes only the validated store and the record. The `VOICEROOM_DEMO_STORE` override is gone.
- Generated audio: the test wav now lives in `$OUT`; wav and reply are deleted only if this run created them (a pre-existing file survives).
- `smoke_demo_script.py` now covers a symlinked store, a `/../` path and the personal store itself (all refused, no commands run, personal data intact), a good resume (explicit `--store`, store removed), and a pre-existing `$OUT/hello.wav` surviving. It touches nothing in the checkout.
- Verified: `make check` green (26 scripts, 0 failed).

## Fix round 4
- `docs/demo-macos.md`: the manual checklist now creates its store with `mktemp -d` (`$VOICEROOM_STORE`) and a separate `mktemp -d` for audio (`$DEMO_AUDIO`); the same variables are used by every command and cleanup. No predictable `/tmp` path remains.
- `scripts/live/openai_realtime_check.py`: new `--out` option (default unchanged).
- `run_macos_demo.sh`: the test clip and reply go to a fresh `mktemp -d` dir (removed at cleanup), so earlier audio is never overwritten. Playback and the "intelligible" result require the check to exit 0 and produce a non-empty reply in that dir; otherwise the failure is recorded and no reply is played.
- `smoke_demo_script.py` now also runs the full (non-`--demo-only`) branch with stubbed `say`/`afplay`/API, both success and failure: asserts no `data/` paths are used, no playback on failure, temp dirs are removed.
- Verified: `make check` green.
