# T-021 summary
**Changed**
- `src/voiceroom/cli.py` + `[project.scripts] voiceroom`: `enroll` (consent prompt or `--yes`; mic ~25 s or `--file`; refuses duplicate names without `--force`; reserved name `Unknown` rejected), `list`, `delete`, `demo` (`--mode`, `--owner`, `--provider openai|mock`, `--input`, `--output-device`/`--out`, `--embedder`, `--threshold`, `--unknown`, `--realtime`). Store dir: `--store`/`VOICEROOM_STORE`; key: `VOICEROOM_KEY`/`--keyfile`. One line per turn: times, speaker, decision, latency (no audio/embeddings).
- `scripts/smoke/smoke_cli.py`: subprocess enroll x2 -> list -> demo isolation (mock, toy, synthetic conv; only owner turns committed) -> error cases -> delete -> list empty.

**Verified**: `make check` green (21 scripts, 0 failed, ~74 s). Reviewer's 2 MEDIUM (budget raise reverted; clean error for unreadable file) and LOWs fixed.

**Notes / follow-ups**
- Total smoke time (~74 s) already exceeds the policy's 60 s (pre-existing; smoke_cli adds ~11 s).
- Real mic/speaker/OpenAI paths untested here (no hardware, no network); needs the macOS human check (M1 checklist).
- File-input demo uses `max_pending=1000` unless `--realtime`, else fast replay overflows.
- `sounddevice` still not declared in pyproject (see T-020).
