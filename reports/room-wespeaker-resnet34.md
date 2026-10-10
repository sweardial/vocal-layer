# Room benchmark: wespeaker-resnet34

- dataset: LibriSpeech dev-clean
- config: conversations=4/variant turns=12 seed=0 speed=1x jobs=4 tracks=3 own + 1 shared (2 members) sweep_conversations=12 enroll_utts=5(+4 narrowband) VAD=SileroVAD MockProvider
- wall time: 223s

## Summary (verified tracks use the config verify threshold)

| variant | owner FR >= 1 s | impostor accept | bleed dup (verified / shared track) | attribution own / shared | commit p50 | commit p95 | queue wait p95 |
|---|---|---|---|---|---|---|---|
| clean | 0.0% (0/21) | 0.0% (0/7) | n/a / n/a | 96.3% (26/27) / 100.0% (7/7) | 419 ms | 444 ms | 0 ms |
| bleed | 0.0% (0/21) | 0.0% (0/7) | 0.0% (0/75) / 0.0% (0/27) | 96.3% (26/27) / 100.0% (7/7) | 490 ms | 637 ms | 8 ms |
| bleed+narrowband | 17.6% (3/17) | 0.0% (0/7) | 0.0% (0/65) / 0.0% (0/22) | 77.3% (17/22) / 71.4% (5/7) | 484 ms | 587 ms | 6 ms |

Commit latency = end of speech -> send_turn done, queue wait included (all variants, n=132): p50 442 ms, p95 601 ms vs 700 ms target: **MEETS**. 0 of 420 pipeline records were errors.

## clean

Owner false rejection on own track, by segment length:

| segment length | FR |
|---|---|
| < 1 s | 16.7% (1/6) |
| 1-2 s | 0.0% (0/9) |
| 2-4 s | 0.0% (0/9) |
| >= 4 s | 0.0% (0/3) |
| all | 3.7% (1/27) |

Impostor on a member's track, accepted (any audio in a committed turn): imp_member 0.0% (0/6), imp_stranger 0.0% (0/1)

## bleed

Owner false rejection on own track, by segment length:

| segment length | FR |
|---|---|
| < 1 s | 16.7% (1/6) |
| 1-2 s | 0.0% (0/9) |
| 2-4 s | 0.0% (0/9) |
| >= 4 s | 0.0% (0/3) |
| all | 3.7% (1/27) |

Impostor on a member's track, accepted (any audio in a committed turn): imp_member 0.0% (0/6), imp_stranger 0.0% (0/1)

Bleed duplicates: a bleed copy of someone's turn (-12 dB) on another track that still reached the model as a turn of its own. shared track 0.0% (0/27), verified track 0.0% (0/75)

## bleed+narrowband

Owner false rejection on own track, by segment length:

| segment length | FR |
|---|---|
| < 1 s | 40.0% (2/5) |
| 1-2 s | 25.0% (2/8) |
| 2-4 s | 12.5% (1/8) |
| >= 4 s | 0.0% (0/1) |
| all | 22.7% (5/22) |

Impostor on a member's track, accepted (any audio in a committed turn): imp_member 0.0% (0/6), imp_stranger 0.0% (0/1)

Bleed duplicates: a bleed copy of someone's turn (-12 dB) on another track that still reached the model as a turn of its own. shared track 0.0% (0/22), verified track 0.0% (0/65)

## Verify threshold sweep

Whole-turn 1:1 score of the claimed member, no VAD, no continuity check; identification threshold 0.35; a turn that confidently matches another member is rejected. Config verify threshold: 0.300. Sequential conversations: the loud audio on a track is bleed-free, so bleed does not change these scores.

| variant | verify thr | owner FR >= 1 s | impostor accept | member | stranger |
|---|---|---|---|---|---|
| clean | 0.350 (-0) | 0.0% (0/58) | 0.0% (0/18) | 0.0% (0/16) | 0.0% (0/2) |
| clean | 0.325 (-0.025) | 0.0% (0/58) | 0.0% (0/18) | 0.0% (0/16) | 0.0% (0/2) |
| clean | 0.300 (-0.05) | 0.0% (0/58) | 0.0% (0/18) | 0.0% (0/16) | 0.0% (0/2) |
| clean | 0.275 (-0.075) | 0.0% (0/58) | 0.0% (0/18) | 0.0% (0/16) | 0.0% (0/2) |
| clean | 0.250 (-0.1) | 0.0% (0/58) | 0.0% (0/18) | 0.0% (0/16) | 0.0% (0/2) |
| clean | 0.225 (-0.125) | 0.0% (0/58) | 0.0% (0/18) | 0.0% (0/16) | 0.0% (0/2) |
| clean | 0.200 (-0.15) | 0.0% (0/58) | 0.0% (0/18) | 0.0% (0/16) | 0.0% (0/2) |
| clean | 0.150 (-0.2) | 0.0% (0/58) | 5.6% (1/18) | 0.0% (0/16) | 50.0% (1/2) |
| bleed+narrowband | 0.350 (-0) | 0.0% (0/52) | 0.0% (0/17) | 0.0% (0/13) | 0.0% (0/4) |
| bleed+narrowband | 0.325 (-0.025) | 0.0% (0/52) | 0.0% (0/17) | 0.0% (0/13) | 0.0% (0/4) |
| bleed+narrowband | 0.300 (-0.05) | 0.0% (0/52) | 0.0% (0/17) | 0.0% (0/13) | 0.0% (0/4) |
| bleed+narrowband | 0.275 (-0.075) | 0.0% (0/52) | 0.0% (0/17) | 0.0% (0/13) | 0.0% (0/4) |
| bleed+narrowband | 0.250 (-0.1) | 0.0% (0/52) | 0.0% (0/17) | 0.0% (0/13) | 0.0% (0/4) |
| bleed+narrowband | 0.225 (-0.125) | 0.0% (0/52) | 0.0% (0/17) | 0.0% (0/13) | 0.0% (0/4) |
| bleed+narrowband | 0.200 (-0.15) | 0.0% (0/52) | 0.0% (0/17) | 0.0% (0/13) | 0.0% (0/4) |
| bleed+narrowband | 0.150 (-0.2) | 0.0% (0/52) | 0.0% (0/17) | 0.0% (0/13) | 0.0% (0/4) |
