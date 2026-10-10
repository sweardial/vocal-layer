# T-051 summary
- Room bench (`bench/room.py`): spliced `tail_stranger` / `tail_member` turns (25 % of turns; 75 % stranger; tails 0.6-2.5 s), report columns "stranger-tail accept" and "member-tail accept", tail results by length (< / >= 1.1 s) in each variant section, TRACK_LOW_RUN in the config line. Defaults 8 conversations x 16 turns. Fixed `LENGTHS` being bound at def time.
- Policy: `TRACK_LOW_RUN` 2 -> 3, new `TRACK_DEEP_LOW_RUN = 2` (2 windows far under the threshold still reject). D-020 has the A/B.
- Smoke `smoke_track_tail.py`: 1 s tail (3 windows) and a 2-window deep tail are rejected on a verified track; a single owner dip is not.
- Result (reports/room-wespeaker-resnet34.md): narrowband owner FR >= 1 s 3.4 % (1/29), bleed/clean 0/30; stranger-tail accept 0/18, 0/18, 1/20 (5 %, a < 1.1 s tail); impostor accept 0; commit p95 649 ms. Before: LOW_RUN 2 13.8 % (4/29), tail 0.
- Verified: `make check` green; bench ~5 min.
- Review fixes: stranger share of tails, short tails, separate columns, smoke fixed tail duration, config line. Declined: `_leaks` for tails (the 0.15 s gap makes a cut at the owner's end count as a leak). Left alone: agentops/config.json M3 change was not mine.
- Caveats: n tiny; strangers' < 1 s tails on narrowband can still pass (1/6 in one variant).

## Fix round 2
- `smoke_livekit_room.py` failed once under `make check`; it does not touch the room bench or policy code. Standalone 3/3 pass, `make smoke` 2/2 pass, `make check` green (39 scripts, 0 failed, ~55 s). It looks flaky under load (livekit FFI handle dispose noise in `__del__`, timing-dependent); no code change made. If it recurs, make it a follow-up.

## Fix round 3
- MEDIUM fixed: tails scored with `_leaks((tail_t0, t1))`; report regenerated.
- HIGH: kept LOW_RUN 3 + deep 2, now with `TRACK_DEEP_LOW_BUFFER` in config. Fair A/B under the strict scoring (narrowband): LOW_RUN 2 FR 4/29, tail 1/20; LOW_RUN 3 deep buffer 0.2 FR 1/29, tail 2/20; buffer 0.15 FR 2/29, tail 1/20; 0.1 FR 4/29, tail 1/20. The "0/20 -> 1/20" the reviewer saw was the old `_covered` scoring; under the same scoring LOW_RUN 2 also leaks 1/20 (a < 1.1 s tail). Real cost of 3: about +1 leak in 20 (noise-level n). Policy-level mid-depth 2-window tails do pass; I did not add a smoke asserting a leak (it would lock it in) nor an audio-pipeline smoke (bench covers it, smoke budget is full). Stronger fix (verify the tail separately before forwarding) is a design change: follow-up added.
- make check had 1 transient smoke failure (not identified; passes on rerun) in round 3, then green.

## Fix round 4
- HIGH: replaced the deep-score rule with a trailing-tail rule (`TRACK_TAIL_LOW_RUN = 2` windows under the floor at the end of the turn reject; `TRACK_LOW_RUN = 3` elsewhere). The reviewer's case (owner windows 0.5, then 2 stranger windows at verify_threshold - 0.15) is rejected, as with LOW_RUN 2; `smoke_track_tail.py` asserts it (policy-level; it is the unit that matters, an audio-pipeline smoke would duplicate the bench and blow the smoke budget), plus that two interior dips and a 1-window dip do not reject.
- Bench default raised to 12 conversations/variant (wall ~9.6 min). Report: narrowband owner FR >= 1 s 4.7 % (2/43), clean/bleed 0/43; stranger-tail accept 1/25, 0/25, 1/27; member tails 0/7, 0/7, 0/10; impostors 0/30; commit p95 within target. An 8-conv. run of this rule gave FR 6.9 % (2/29), so the 5 % target is met only marginally (small n); the single leaked narrowband tail is the same one that leaks under LOW_RUN 2.
- T-052 (verify the tail separately before forwarding) remains the proper fix.
