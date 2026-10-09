# T-021 review

`make check`: green (ruff clean; 21 smoke scripts pass; smoke_cli.py 13.1 s; total 75.6 s).

## Findings

[MEDIUM] scripts/smoke/run_all.py:12: the budget goes up to 90 s, but `.work/POLICY.md` (set by the human) says smoke scripts must take "< 60 s total". The measured total is 75.6 s, so it already fails the old 75 s budget, and smoke_cli.py adds 13 s on its own → each task can keep raising the limit quietly, and the human's runtime rule is no longer enforced → keep 75 or lower. Make smoke_cli cheaper: fewer subprocess launches (for example, drop the collaboration demo or the refused-consent run, or merge the delete/list checks), or reuse one shorter conversation. Alternatively, write `needs_human.md` asking for a new budget instead of changing it here.

[MEDIUM] src/voiceroom/cli.py:90: `load_audio(args.file)` is outside any handler, and `main()` (line 307) only catches CliError/StoreError/OSError. `soundfile.LibsndfileError` is a RuntimeError → reproduced: `voiceroom enroll --name x --file /nonexistent.wav --yes --embedder toy` prints a raw traceback instead of `error: ...`. The same happens for a typo'd path or an unsupported or corrupt file. `demo --input` has the same issue, but there the error is wrapped as CliError by the line 252 catch-all. → In cmd_enroll, wrap `load_audio` in `try/except (RuntimeError, OSError) as e: raise CliError(f"cannot read {args.file}: {e}")`. Ideally check the file before the consent prompt.

[LOW] src/voiceroom/cli.py:101: `store.save` silently overwrites any existing signature with the same name → if `voiceroom enroll --name alice` is run by someone else (or by mistake), alice's signature and her consent record are replaced, and isolation mode then forwards the new speaker as the owner. Nothing warns about it. → If `store.list()` already contains the name, refuse unless `--force` is given (or ask in the interactive prompt).

[LOW] src/voiceroom/cli.py:221: if someone enrolls the name `Unknown`, `CollaborationPolicy` raises ValueError (policy.py:111), which is not caught → `voiceroom demo --mode collaboration` crashes with a traceback. → Reject the reserved name `Unknown` in cmd_enroll, or catch ValueError around policy construction and raise CliError.

[LOW] src/voiceroom/cli.py:94: Ctrl-C during the 25 s mic enrollment raises KeyboardInterrupt out of `asyncio.run`, and nothing catches it → the user sees a traceback instead of "cancelled; nothing stored". Nothing is saved, so this is cosmetic. → Catch KeyboardInterrupt in cmd_enroll and raise CliError("cancelled; nothing recorded").

[LOW] src/voiceroom/cli.py:142,157-161: if the sink's `play` task fails mid-session (for example, a PortAudio output error), nobody notices until `close()`. The demo keeps printing `committed` turns while the user hears nothing. → Add a done-callback on `_task` that writes a one-line warning to stderr (fixed text, no payload).

[LOW] scripts/smoke/smoke_cli.py:49-50: if the `voiceroom` script is missing from the venv (for example, the env was not re-synced after the `[project.scripts]` change), the smoke silently falls back to `python -m voiceroom.cli` → the acceptance item "a `[project.scripts] voiceroom` entry point" can pass while the entry point is broken. It exists in the current env (`~/.venv-voiceroom/bin/voiceroom`), so this only matters on other machines. → Assert that `exe.exists()`, or check `importlib.metadata.entry_points(group="console_scripts")` for `voiceroom = voiceroom.cli:main`.

## Checked, no issue
- Privacy: turn lines print only times, name, decision, latency and a fixed error code. No audio or embeddings are printed or logged, and the store is Fernet-encrypted with HMAC filenames (the smoke checks this). No pickle/torch.load on stored data.
- Consent: refused when there is no tty and no `--yes` (the smoke covers this); the prompt defaults to No.
- No provider-specific code outside adapters (the CLI only picks between the MockProvider and OpenAIRealtimeProvider classes). The smoke removes OPENAI_API_KEY and uses the mock, so it makes no network calls.
- Silence or too-short enrollment gives a clean `enrollment failed: need >= 10 s of speech` (verified). Sample-rate and stereo input are handled by load_audio/FileSource resampling. An empty store and a non-enrolled owner are rejected with clear errors.
- The smoke asserts that committed isolation turns overlap only alice's segments and that at least 80 % of owner turns are committed. This does not pass trivially: an empty output fails `assert committed`.
