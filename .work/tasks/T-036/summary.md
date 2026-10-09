# T-036 summary
- `policy._continuity_bad` refactored into `_continuity_rule` returning the rule name (`foreign-named`, `bad-run`, `all-bad`, `deep-run`, `short-foreign-run`, `short-low-run`, `unknown-matches-enrolled`) or None; decisions unchanged. Policies gain `windows_rule` + `describe_windows` (`windows_ok` kept).
- Pipeline logs (INFO, `voiceroom.pipeline`) one line per continuity-dropped turn: rule, who, short, threshold, floor, per-window score/name. Numbers and names only.
- `voiceroom demo --debug` enables that logger on stderr.
- Verified: `make check` green (26 scripts); smoke_e2e_isolation asserts the log line on a synthetic owner+stranger turn; smoke_windows_bench stub updated.
- Follow-up (human): rerun the demo with `--debug --no-cohort`, read the rule/scores of dropped owner turns.
- Not logged: whole-turn match rejects (not continuity).
