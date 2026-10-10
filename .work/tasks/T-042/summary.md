# T-042 summary

## Changed
- `src/voiceroom/bench/room.py` (new): `python -m voiceroom.bench.room`. 3 own-mic participants + 2 members on a shared mic + stranger; 20 % impostor turns on participants' tracks; clean / bleed (-12 dB) / bleed+narrowband variants; realtime through RoomPipeline (SileroVAD, wespeaker, MockProvider). Writes `reports/room-wespeaker-resnet34.md` (owner FR by length, impostor accept, bleed dup, attribution, commit p50/p95 incl. queue wait, verify-threshold sweep). Added to `make bench`.
- `scripts/smoke/smoke_room_bench.py`: 1-conversation run, SKIP if assets absent (structural asserts only; paced 4x so identify_busy drops happen).
- `config.TRACK_LOW_RUN` (= 2, no behaviour change) wired through `policy._Lenient`. D-019 records calibration: verify threshold kept at 0.30; bleed thresholds kept; low-run 3 tried and rejected (leaks stranger tails, unmeasurable here).

## Verified
- bench.room: 3m44s. Owner FR >= 1 s: 0/21 clean, 0/21 bleed, **3/17 narrowband (misses < 5 %)**; impostor accept 0/7 each; bleed dup 0/75 (shared 0/27); commit p95 601 ms (MEETS 700).
- `make check` green.

## Follow-up
- T-051: spliced stranger-tail cases + narrowband FR. Small n; shared-mic narrowband attribution 71 % (5/7).
