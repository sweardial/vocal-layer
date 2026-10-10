# Room benchmark: wespeaker-resnet34

- dataset: LibriSpeech dev-clean
- config: conversations=12/variant turns=16 seed=0 track_low_run=3 p_tail=0.25 speed=1x jobs=4 tracks=3 own + 1 shared (2 members) sweep_conversations=12 enroll_utts=5(+4 narrowband) VAD=SileroVAD MockProvider
- wall time: 578s

## Summary (verified tracks use the config verify threshold)

| variant | owner FR >= 1 s | impostor accept | stranger-tail accept | member-tail accept | bleed dup (verified / shared track) | attribution own / shared | commit p50 | commit p95 | queue wait p95 |
|---|---|---|---|---|---|---|---|---|---|
| clean | 0.0% (0/43) | 0.0% (0/30) | 4.0% (1/25) | 0.0% (0/7) | n/a / n/a | 98.3% (57/58) / 97.4% (37/38) | 418 ms | 452 ms | 12 ms |
| bleed | 0.0% (0/43) | 0.0% (0/30) | 0.0% (0/25) | 0.0% (0/7) | 0.0% (0/230) / 0.0% (0/58) | 96.6% (56/58) / 100.0% (38/38) | 500 ms | 690 ms | 50 ms |
| bleed+narrowband | 4.7% (2/43) | 0.0% (0/30) | 3.7% (1/27) | 0.0% (0/10) | 0.0% (0/231) / 1.9% (1/54) | 94.4% (51/54) / 70.7% (29/41) | 496 ms | 649 ms | 30 ms |

Commit latency = end of speech -> send_turn done, queue wait included (all variants, n=491): p50 463 ms, p95 649 ms vs 700 ms target: **MEETS**. 0 of 1828 pipeline records were errors.

## clean

Owner false rejection on own track, by segment length:

| segment length | FR |
|---|---|
| < 1 s | 6.7% (1/15) |
| 1-2 s | 0.0% (0/14) |
| 2-4 s | 0.0% (0/22) |
| >= 4 s | 0.0% (0/7) |
| all | 1.7% (1/58) |

Impostor on a member's track, accepted (any audio in a committed turn; tail_*: other voice spliced after the owner in one turn, same rule on the tail part): imp_member 0.0% (0/24), imp_stranger 0.0% (0/6), tail_member <1.1 s 0.0% (0/3), tail_member >=1.1 s 0.0% (0/4), tail_stranger <1.1 s 0.0% (0/10), tail_stranger >=1.1 s 6.7% (1/15)

## bleed

Owner false rejection on own track, by segment length:

| segment length | FR |
|---|---|
| < 1 s | 13.3% (2/15) |
| 1-2 s | 0.0% (0/14) |
| 2-4 s | 0.0% (0/22) |
| >= 4 s | 0.0% (0/7) |
| all | 3.4% (2/58) |

Impostor on a member's track, accepted (any audio in a committed turn; tail_*: other voice spliced after the owner in one turn, same rule on the tail part): imp_member 0.0% (0/24), imp_stranger 0.0% (0/6), tail_member <1.1 s 0.0% (0/3), tail_member >=1.1 s 0.0% (0/4), tail_stranger <1.1 s 0.0% (0/10), tail_stranger >=1.1 s 0.0% (0/15)

Bleed duplicates: a bleed copy of someone's turn (-12 dB) on another track that still reached the model as a turn of its own. shared track 0.0% (0/58), verified track 0.0% (0/230)

## bleed+narrowband

Owner false rejection on own track, by segment length:

| segment length | FR |
|---|---|
| < 1 s | 9.1% (1/11) |
| 1-2 s | 0.0% (0/14) |
| 2-4 s | 4.5% (1/22) |
| >= 4 s | 14.3% (1/7) |
| all | 5.6% (3/54) |

Impostor on a member's track, accepted (any audio in a committed turn; tail_*: other voice spliced after the owner in one turn, same rule on the tail part): imp_member 0.0% (0/22), imp_stranger 0.0% (0/8), tail_member <1.1 s 0.0% (0/5), tail_member >=1.1 s 0.0% (0/5), tail_stranger <1.1 s 11.1% (1/9), tail_stranger >=1.1 s 0.0% (0/18)

Bleed duplicates: a bleed copy of someone's turn (-12 dB) on another track that still reached the model as a turn of its own. shared track 1.9% (1/54), verified track 0.0% (0/231)

## Verify threshold sweep

Whole-turn 1:1 score of the claimed member, no VAD, no continuity check; identification threshold 0.35; a turn that confidently matches another member is rejected. Config verify threshold: 0.300. Sequential conversations: the loud audio on a track is bleed-free, so bleed does not change these scores.

| variant | verify thr | owner FR >= 1 s | impostor accept | member | stranger |
|---|---|---|---|---|---|
| clean | 0.350 (-0) | 0.0% (0/43) | 0.0% (0/30) | 0.0% (0/24) | 0.0% (0/6) |
| clean | 0.325 (-0.025) | 0.0% (0/43) | 0.0% (0/30) | 0.0% (0/24) | 0.0% (0/6) |
| clean | 0.300 (-0.05) | 0.0% (0/43) | 0.0% (0/30) | 0.0% (0/24) | 0.0% (0/6) |
| clean | 0.275 (-0.075) | 0.0% (0/43) | 0.0% (0/30) | 0.0% (0/24) | 0.0% (0/6) |
| clean | 0.250 (-0.1) | 0.0% (0/43) | 0.0% (0/30) | 0.0% (0/24) | 0.0% (0/6) |
| clean | 0.225 (-0.125) | 0.0% (0/43) | 3.3% (1/30) | 0.0% (0/24) | 16.7% (1/6) |
| clean | 0.200 (-0.15) | 0.0% (0/43) | 3.3% (1/30) | 0.0% (0/24) | 16.7% (1/6) |
| clean | 0.150 (-0.2) | 0.0% (0/43) | 3.3% (1/30) | 0.0% (0/24) | 16.7% (1/6) |
| bleed+narrowband | 0.350 (-0) | 0.0% (0/43) | 0.0% (0/30) | 0.0% (0/22) | 0.0% (0/8) |
| bleed+narrowband | 0.325 (-0.025) | 0.0% (0/43) | 0.0% (0/30) | 0.0% (0/22) | 0.0% (0/8) |
| bleed+narrowband | 0.300 (-0.05) | 0.0% (0/43) | 0.0% (0/30) | 0.0% (0/22) | 0.0% (0/8) |
| bleed+narrowband | 0.275 (-0.075) | 0.0% (0/43) | 6.7% (2/30) | 0.0% (0/22) | 25.0% (2/8) |
| bleed+narrowband | 0.250 (-0.1) | 0.0% (0/43) | 6.7% (2/30) | 0.0% (0/22) | 25.0% (2/8) |
| bleed+narrowband | 0.225 (-0.125) | 0.0% (0/43) | 6.7% (2/30) | 0.0% (0/22) | 25.0% (2/8) |
| bleed+narrowband | 0.200 (-0.15) | 0.0% (0/43) | 6.7% (2/30) | 0.0% (0/22) | 25.0% (2/8) |
| bleed+narrowband | 0.150 (-0.2) | 0.0% (0/43) | 13.3% (4/30) | 0.0% (0/22) | 50.0% (4/8) |
