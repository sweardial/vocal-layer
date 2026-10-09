# T-029 review

`make check`: green (lint OK, 25 smokes, 0 failed). Total smoke time was 81.95 s, which is over the policy's 60 s. That overrun was already there and is tracked in T-030. This diff adds about 0 s to it.

## HIGH

[HIGH] src/voiceroom/config.py:23 (+ policy.py:94): Setting `WINDOW_BAD_RUNS` to 4 by default makes the isolation continuity check far weaker. Per the task's own sweeps, leak of interior interjections goes from 1.1 % (D-014: drop 0.3, run 1; 0.5 s 3.3 %, 1 s 0 %) to 33.3 % (`reports/windows-wespeaker-resnet34-run4.md`, buffer 0.1, drop 0.3/1.0 rows: 0.5 s 83 %, 1 s 15 %). SPEC line 49 requires isolation false-accept (non-owner speech forwarded) < 2 %. The pipeline bench's "FA 0/43" never measures this, because its conversations contain no interior interjections. Only whole non-owner segments are scored. The result also misses the bar already set in T-031 (leak <= 5 % at >= 1 s).
  → Failure scenario: the owner is talking to the assistant and a colleague cuts in with a 1 s "no, wipe the staging DB". About 15 % of the time that 1 s reaches the model in isolation mode, and a 0.5 s cut-in gets through about 83 % of the time. Before this change the 1 s case was about 0 %.
  → Fix: don't ship run=4 as the default against the SPEC FA target without the owner signing off. Either keep run=1 as the default (put the relaxed rule behind config or a flag for T-031), or combine the run rule with a guard that bounds leak (for example, reject if any window's best candidate is a different *enrolled* speaker above threshold, or use a bad-fraction rule). If no setting meets both FR < 5 % and leak < 2 % (the sweep suggests none does), write `needs_human.md` with the trade-off rather than picking one silently.

## MEDIUM

[MEDIUM] policy.py:94 / pipeline.py:286-303: The continuity check can't reject a turn that has fewer than 4 scored windows, even if every window is bad. The pipeline scores windows at a 0.25 s hop plus one window anchored at the end, so turns with less than about 1.0-1.25 s of voiced speech produce at most 3 windows. Windows below `MIN_VOICED` are dropped from the list, which reduces the count further.
  → Failure scenario: a 1.1 s turn with 0.4 s of owner speech followed by 0.7 s of a stranger. The whole-turn match picks the owner, all 3 windows are bad, `_longest_run` = 3 < 4, and the turn is forwarded. The same happens in `CollaborationPolicy.windows_ok`: a 3-window turn labelled "alice" whose windows are all "bob" passes.
  → Fix: also reject when the share of bad windows reaches a threshold (for example >= 50 %, or all windows bad when there are fewer than `window_bad_run`). Alternatively, set `bad_run = min(window_bad_run, len(matches))`.

[MEDIUM] policy.py:94 + pipeline.py:296-297: "Consecutive" means consecutive in the `matches` list, not in time. Unvoiced windows are skipped with `continue`, so bad windows on either side of a pause are joined into one run. The end-anchored window overlaps the last regular window by up to 0.47 s, so at the end of a turn, 4 windows can cover less than 1.25 s. The config comment ("4 in a row = a sustained ~1.25 s foreign span") is therefore wrong in the pipeline, and the windows bench (no VAD, no skipped windows) doesn't model either effect.
  → Failure scenario: an owner turn with two short noisy patches (2 bad windows each) separated by a 0.5 s pause is falsely rejected as a "run of 4".
  → Fix: pass the window start frames to `windows_ok` (or put `None` for skipped windows to break runs), and run the check on that time-aligned sequence.

[MEDIUM] .work/decisions.md D-015 / reports/pipeline-wespeaker-resnet34.md: Acceptance claims owner FR < 5 % from 0/17 segments (95 % upper bound ~19.5 %). The larger offline sweep at the exact shipped operating point (buffer 0.1, drop 0.4, the same as the 0.3/1.0 rows) gives 8.3 % (5/60), which is above the 5 % target. The 0/17 is probably flattered by segment splitting (95 turns from 60 segments): shorter split turns have fewer than 4 windows and can't fail (see above).
  → Failure scenario: the harness marks T-029 done on a number that its own better-powered bench contradicts, and the live FR comes in around 8 %.
  → Fix: in D-015, state that FR is about 8 % on the windows bench and the target is not met there. Re-run the pipeline bench with more owner segments, or record that the accept criterion was met only on a small sample.

[MEDIUM] src/voiceroom/config.py:18,23: The ecapa-voxceleb values (drop 0.4, run 4) were set with no run-4 measurement for ecapa. Only `windows-wespeaker-resnet34-run4.md` exists, and D-014 notes that ecapa behaves differently (buffer 0.2 gives 3.3 % leak for ecapa).
  → Failure scenario: selecting ecapa ships an uncalibrated continuity rule whose leak and FR are unknown.
  → Fix: run `python -m voiceroom.bench.windows --embedder ecapa-voxceleb --bad-run 4` and commit the report, or leave ecapa at the previously calibrated values (0.3, run 1).

## LOW

[LOW] src/voiceroom/bench/pipeline.py:317 / reports/pipeline-wespeaker-resnet34.md: The report header records `window_drop=config` but not `window_bad_run`, and it was run at `speed=3x` while the latency section is titled "realtime pacing".
  → Failure scenario: someone comparing reports after a config change can't tell which continuity rule produced them.
  → Fix: add the resolved `window_drop` and `window_bad_run` values to the header, and use the real pacing in the section title.

[LOW] src/voiceroom/bench/windows.py:29: `DROPS` doesn't include the shipped 0.4, so the committed sweep never shows the default operating point directly. It is only inferred from the matching 0.3 and 1.0 rows.
  → Fix: add 0.4 to `DROPS`, or note in the report that the 0.3 and 1.0 rows bracket the default.

[LOW] policy.py:53,141 / bench/windows.py `--bad-run`: `window_bad_run` isn't validated.
  → Failure scenario: with 0 (or a negative value), `_longest_run(bad) < 0` is always False and every turn is rejected. With None in `CollaborationPolicy` when `bind_matcher` was never called (no embedder name), the comparison raises TypeError. Before this change the Unknown branch didn't need any calibrated value.
  → Fix: require `window_bad_run >= 1` in `__init__`, and in `bind_matcher` default to `FALLBACK_WINDOW_BAD_RUN` instead of leaving it None.

[LOW] policy.py:187: The collaboration Unknown branch now lets up to 3 consecutive windows match an enrolled speaker. No smoke covers this changed branch: `smoke_windows_bench` only exercises `IsolationPolicy`.
  → Failure scenario: an enrolled speaker's ~1 s of speech inside a stranger's turn is sent labelled "Unknown", and nothing would catch a regression.
  → Fix: add one Unknown-branch assertion to `smoke_windows_bench` (accept with run < N, reject with run >= N).
