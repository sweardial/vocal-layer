# Review T-032 (round 1)

Verdict: FAIL. `make check` is red, and an A/B against HEAD shows the failures come from this diff. I ran the smokes from a temp worktree at HEAD with the same venv.

Evidence:
- `make check` on the working tree, run twice: 2-3 failures each time. `smoke_pipeline_real` failed every time (3/3 suite runs, commit latencies 2.85-3.32 s against an assert of < 3.0 s). `smoke_e2e_isolation` also failed (`['identify_busy', 'send_error']`, `(6, 7)` owner turns).
- HEAD, full suite run twice: `smoke_pipeline_real` passes. `smoke_bench` failed once on timing (10.2 s against < 10 s); that flake predates this diff.
- `smoke_e2e_isolation` run alone: working tree failed 2/6 runs, HEAD failed 0/6.
- `smoke_pipeline_real` run alone: working tree latencies 0.55-0.76 s, HEAD 0.42-0.46 s.

## Findings

[HIGH] src/voiceroom/pipeline.py:33 (HOP_FRAMES = 4) with :152 has_room / :196 window burst / :310 missing-window identify: identify slots run out, so owner turns are dropped with `identify_busy`.
→ Halving the hop doubles the live window tasks. `has_room()` is checked once per frame. Once it turns true, the `while n >= nxt + WIN_FRAMES` loop spawns the whole backlog of windows at once, now twice as many. Meanwhile the worker's `_continuity_ok` for the previous turn calls `self._identify` on its missing or end-anchored windows. `_identify` raises `identify_busy` when the next turn's live windows hold the slots, and that drops the whole previous turn. The drain added at :293 only waits on the turn's *own* windows and does nothing about the next turn's. Raising MAX_INFLIGHT to 8 (:30) only moves the limit. Reproduced: `smoke_e2e_isolation` fails 2/6 standalone with `['identify_busy', 'send_error']` or `owner_ok (6, 7)`, and 0/6 on HEAD.
→ Fix: cap how many windows the loop spawns per frame (spawn at most one or two per iteration and let `nxt` catch up). Reserve slots for the worker: live windows only spawn below `max_inflight - k`, and turn-end identifies may use the reserve. Or make the turn-end path wait for a slot (bounded by `identify_timeout_s`) instead of raising. Then make `smoke_e2e_isolation` pass reliably (for example 10/10 runs).

[HIGH] src/voiceroom/pipeline.py:293-296 (drain) + :33 (hop 4): commit latency goes up roughly 6-7x under CPU load, and `smoke_pipeline_real` fails inside `make check`.
→ At TurnEnd the worker now awaits every pending window task before it computes anything, and there are twice as many of them. When the embedder lags behind the stream (CPU contention, slower hardware, or the 6x-realtime smoke), the commit waits for the whole window backlog. `smoke_pipeline_real` commit latency is about 3.0-3.3 s on every suite run (HEAD about 0.45 s, same suite load), so `make check` is red. The 700 ms commit target in SPEC/D-015 is unverified: the pipeline bench was not rerun after these changes, and no pipeline report is in the diff.
→ Fix: don't block the commit on windows the decision doesn't need. Score windows in order and stop early once the rule decides, or start the missing turn-end windows while live windows are still finishing (with the slot reserve from the previous fix). Keep the per-turn window count bounded, for example by keeping hop 8 for live scoring if the benchmark gain allows it. Rerun `python -m voiceroom.bench.pipeline` and record commit p95, owner FR and FA in D-016. `make check` must pass.

[MEDIUM] src/voiceroom/policy.py:99-108 and :228-237 (deep rule; same issue for `bad`'s `m.best != who` term): the deep rule only fires when someone other than the owner is enrolled.
→ `deep` requires `m.best != who`. With only the owner enrolled, `Match.best` is always the owner, so `deep` is always False. The new 0.5 s leak result then reduces to `bad` with run 5 and drop 0.15, which nobody measured in that setup. The windows bench always enrolls 3 speakers (`collect`, enrolled = pick[:3]). The README line "about 30 % of 0.5 s spliced interjections" (D-016) therefore holds only when other people are enrolled. An owner-only isolation session (`voiceroom demo --mode isolation --owner alice`) gets a different leak/FR trade-off, probably worse on leak given the looser drop of 0.15.
→ Fix: also judge deep windows on the absolute score (`score(who) < threshold - DEEP_BUFFER*k`) without requiring that someone else is best, or state the multi-enrollment assumption. Add an owner-only-enrolled row to the windows bench, and correct README "Known limits" if that row differs.

[MEDIUM] reports/windows-wespeaker-resnet34-run5.md + .work/decisions.md D-016: the acceptance result rests on the tuning set, the margin is one turn, and the held-out check isn't committed.
→ Hop, run 5, drop 0.15, DEEP_BAD_RUN 3 and DEEP_BUFFER 0.2 were all picked by sweeping these same 60 owner / 180 interjection turns (seed 0). D-016 admits this. The accept bar is FR < 5 %; the reported 3.3 % is 2/60, and the next drop down (0.12) is already 5.0 % (3/60). The seed-1 check says "see summary", but `.work/tasks/T-032/` has no summary.md and no seed-1 report. A single extra owner false-reject on fresh data would fail the bar. Most of the gain also comes from changing the bench (adding VAD), not the product: D-016 says "most of the old bench's owner FR was pause windows". So "0.5 s leak below 42 %" compares a no-VAD baseline with a VAD run. The run5 report has no VAD row for the old rule, so how much of the 42 -> 30 % comes from the rule change can't be checked.
→ Fix: commit the seed-1 (held-out) report, plus a VAD-aware run of the D-015 rule (hop 8, run 3, drop 0.4, no deep rule) as the like-for-like baseline. Quote both in D-016.

[LOW] src/voiceroom/config.py:18 (wespeaker drop 0.4 -> 0.15): the 1 s interjection leak went up from 3.3 % to 5.0 %.
→ This is exactly the T-031 bar (leak <= 5 % for >= 1 s interjections). Any noise on new data puts 1 s interjections over it, and D-016 doesn't mention the regression. Example: a 1 s interjection by a stranger (14.4 % "leak stranger" overall at this setting) reaches the model.
→ Fix: mention the 1 s leak regression and the T-031 bar in D-016, or pick the drop that keeps 1 s leak at or below 3.3 % (0.1 gives 1.7 % but FR 11.7 %, so state the trade-off).

## Checked, no finding
- No audio or embeddings are logged. The bench VAD is offline, there is no network use, and the code is not provider-specific.
- `_sustained` with the deep flags: the all-scored shortcut implies `bad` is all-true too, so nothing changes. Toy, ecapa and unknown embedders keep bad_run 1, so deep (run 3) is subsumed by it.
- The bench window starts and `_window` match the pipeline, including the end-anchored window.

## Round 2 note: the review.md HIGH findings concerned hop 0.125 s + MAX_INFLIGHT 8 + drain, all reverted; pipeline.py is unchanged vs HEAD. Mediums addressed in D-016/summary (owner-only inertness, seed-1 held-out, baseline comparison).
