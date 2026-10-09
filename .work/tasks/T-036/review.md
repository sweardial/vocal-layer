# T-036 review

Scope: `git diff HEAD` (policy.py, pipeline.py, cli.py, smoke_e2e_isolation.py, smoke_windows_bench.py). No untracked code files. No `plan.md` in the task dir (only `summary.md`).

`make check`: green (ruff clean, 26 smoke scripts, 0 failed, ~40 s).

Decision equivalence: I loaded HEAD's `policy.py` next to the new one and fuzzed 40,000 random window lists. The inputs mixed pauses, missing owner scores, short and long turns, score_scale in {0.5, 1, 3}, window_bad_run 1-4, and isolation and collaboration with label in {owner, other, Unknown, None}. Old and new `windows_ok` matched on every case, `windows_rule(...) is None` matched `windows_ok`, and `describe_windows` never raised on the 18.6k rejects. The rule order (short: foreign then low; long: foreign, then bad-run/all-bad, then deep) keeps the old short-circuit order. No reject decision changed.

Privacy: the log line holds only per-window owner scores, threshold, floor and speaker labels. No audio, no embeddings. `--debug` adds a root handler, but the websockets logger stays isolated (`propagate=False` + NullHandler). Before this change, WARNING lines already reached stderr through logging's lastResort handler, so no new payload logging appears.

## Findings

[LOW] src/voiceroom/policy.py:146: the log line prints speaker labels (`m.name or m.best`, plus `who=`). T-036's goal says "numbers only". → The follow-up asks the human to rerun the demo and share the debug output. That output names every enrolled person each window resembled, including `m.best` for windows below the threshold. Real enrolled names then end up in pasted logs and issue threads. → Log a role instead of the label: `owner`/`self` for `who`, `other` for any other enrolled best, `-` for none. Or log a stable index into the enrolled list.

[LOW] src/voiceroom/policy.py:304 (with :139): for collaboration's `unknown-matches-enrolled` rule, `describe_windows` scores each window against `who="Unknown"`, which is never a key in `m.scores`. → Every window prints as `-inf/<name>` and the floor prints as `thr - buffer`. This makes the line look like a scoring failure, when the actual reason is that a window matched an enrolled speaker. → For the Unknown label, log each window's best enrolled name and score (`m.score`), and print `floor=n/a`.

[LOW] src/voiceroom/cli.py:216-220: no smoke runs `voiceroom demo --debug`. smoke_e2e_isolation configures the `"voiceroom.pipeline"` logger by hand with the same hard-coded string. → If the pipeline module moves or is renamed (the logger comes from `__name__`), or the flag wiring regresses, `--debug` silently prints nothing during the one-shot human live rerun. Every smoke still passes. → Add one case to smoke_cli: run `demo --debug` with the mock provider on a synthetic owner+stranger WAV, and assert that stderr contains `continuity reject: rule=`.

## Counts
HIGH 0, MEDIUM 0, LOW 3
