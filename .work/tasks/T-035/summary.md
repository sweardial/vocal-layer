# T-035 summary

**Cause (inferred):** live alice turns identified as alice yet dropped can only be rejected by `windows_ok` (continuity). On 1-2 s turns there are 3-4 half-second windows, so one odd window trips the single-foreign guard or the all-bad rule. The live log has no window scores, so the exact rule is not measured. Clean offline audio does not reproduce it (owner FR 3.3 % on 1-2 s turns).

**Changed:** turns < `CONTINUITY_MIN_S` (2.0 s) use a lenient `short` continuity rule (D-017): two consecutive foreign-named or far-below-threshold windows reject; collaboration keeps its single-foreign guard. `bench.windows` gained `--narrowband` and `--turn-s`; policy continuity code deduped into `_continuity_bad`.

**Narrowband re-test:** owner FR 10 % (3-6 s) / 5 % (1-2 s), 1 s leak 13 % (clean 3.3 %); a modest cost, not a threshold failure.

**Verified:** `make check` green (26 scripts); pipeline bench (14 convs) FR/FA/attribution/latency unchanged vs reports/. Smokes updated: smoke_windows_bench (short path, owner-only enrollment), smoke_e2e_isolation (0.16-0.5 s tails on short turns now accepted by design).

**Limits:** the offline windows bench does not exercise the short path, so the FR gain and leak cost are not measured; threshold untouched. Reviewer HIGH (owner-only enrollment no-op) and collab MEDIUM fixed. Follow-up T-036 (log the rule that fired).

## Fix round 2
- HIGH: the short rule now also rejects two consecutive windows under the long rule's score floor (`max(threshold - buffer, median - drop)`), independent of rival enrollment. `smoke_e2e_isolation` has owner-only pipeline cases (0.75 s and 0.9 s tails rejected, default and narrow buffers); the unenrolled-tail case is the same setup (every tail speaker is unenrolled).
- MEDIUM: `bench.windows` keeps turn duration, picks `short` via `pipeline.is_short_turn`, and has short owner+tail cases (enrolled and stranger). Result (clean, 30 trials): short-turn owner FR 6.7 % (4/60), tail leak 0.5 s 20 % enrolled / 30 % stranger, 0.75 s 3.3 % / 20 %. Honest note: no FR gain on clean audio (earlier old-rule run 3.3 %, noise-level); the change targets the live-log failures, unverified until T-036 data.
- `make check` green (26 scripts).
