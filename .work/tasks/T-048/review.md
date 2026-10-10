# T-048 review

Scope reviewed: `git diff HEAD` (src/voiceroom/cli.py, agentops/config.json, .work/*) and the untracked scripts/smoke/smoke_cli_room.py.
I ran smoke_cli_room.py on its own (make check was not rerun, as instructed). It passes in about 29 s with the LiveKit part included (binary at data/bin/livekit-server).
Unit tests are OFF per .work/POLICY.md, so none are requested.

Things I checked that hold up: subcommands go through SessionStore/SessionSync from T-046. Room output is limited to fixed fields, and error codes are fixed strings. Invite codes and tokens are not printed by `room`. --debug configures the `voiceroom.pipeline` logger, which is where SourceProcessor (used by RoomPipeline) logs. The scripted wavs go through load_audio, which resamples. OPENAI_API_KEY and LIVEKIT_URL are stripped from the smoke env, so the fake part stays offline.

## Findings

[MEDIUM] scripts/smoke/smoke_cli_room.py:114-115: the `--debug` check only re-asserts the stdout turn format, and it passes without any rule logging → I reran this exact invocation and captured stderr: it is empty. No turn in the script triggers a T-036 continuity rule, because carol is dropped by `decide`, not by the windows rule. If `_debug_logging()` targeted the wrong logger, or if RoomPipeline stopped routing through SourceProcessor, the smoke would still pass. That leaves the accept criterion "--debug reuses the T-036 rule logging" unproven, and T-050's live checklist depends on it → script one turn that fires a rule (for example alice's verified track with a carol tail spliced on, or a short mixed turn). Assert that stderr has at least one rule line, and that the line holds no embedding vectors and no invite codes.

[MEDIUM] scripts/smoke/smoke_cli_room.py:159-183: the LiveKit part uses a fixed `--duration 9` timer that starts when the agent starts. After the bot joins, the test makes 3 sequential blocking `voiceroom token` subprocess calls (about 0.9 s each when the machine is idle), sleeps 0.5 s, and then publishes 3.5 s of audio in real time → this run took 10.3 s, so the margin before the agent leaves is only about 2 s. Under `run_all.py` (4 scripts at once, 1 BLAS thread each), slower CLI startup can push alice's or bob's end of speech past the deadline. `committed(lk, "alice") and committed(lk, "bob")` then fails at random → mint all tokens before launching the agent (they don't depend on it), and stop the agent with SIGINT after the publishers disconnect (cmd_room already returns 0 on KeyboardInterrupt). Alternatively, use a much larger --duration together with the SIGINT.

[LOW] scripts/smoke/smoke_cli_room.py:119: after the revoke, the smoke only checks `not committed(turns, "bob")`, and the spec says "bob dropped" → the check also passes if bob produces no turns at all, for example if his script entry breaks or a kick stops his track before he speaks. In that case the revoked-member drop path is never exercised. In my rerun bob does produce 2 `dropped` turns, but nothing asserts it → add `assert any(t[1] == "bob" and t[2] == "dropped" for t in turns)`.

[LOW] src/voiceroom/cli.py:350: `if args.ttl_hours` treats `--ttl-hours 0` (and any value that rounds to falsy) as "unset" → a user who asks for a 0-hour or immediately expiring invite silently gets a 24 h single-use secret instead. A negative value is passed through unchecked → use `is not None` and reject values <= 0 with a CliError. The same falsy pattern at cli.py:480 makes `--duration 0` run forever.

[LOW] scripts/smoke/smoke_cli_room.py (whole file): it takes about 29 s on its own, which is roughly half of run_all's 60 s total budget (BUDGET_S). Most of that is about 15 CLI subprocesses at about 0.9 s each, plus the LiveKit section → future smokes will push `make check` over budget → reuse one invocation where possible. For example, the `--debug` room run can replace the first plain room run once it carries a rule-triggering turn.

[LOW] .work/tasks/T-048/: the directory is empty. There is no plan.md, even though agentops/config.json has `require_plan_approval: true` → the change landed without an approved plan, so the review can't be checked against agreed scope → confirm with the harness that the plan was approved, or that this was intentionally skipped.

[LOW] agentops/config.json: adds "M3" to `milestones`. This is not part of T-048 → if an agent made this edit rather than the harness, it changes which tasks the harness schedules → revert it from this task's diff unless the harness made it.
