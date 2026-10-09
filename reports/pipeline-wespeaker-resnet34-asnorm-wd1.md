# Pipeline benchmark: wespeaker-resnet34

- dataset: LibriSpeech dev-clean
- config: conversations=4 turns=6 seed=0 speed=1x window_drop=1.0 enroll_utts=5 VAD=SileroVAD
- wall time: 386s

## Latency: end of speech -> commit (isolation pipeline, realtime pacing)

- 24 ground-truth segments -> 40 pipeline turns (9 committed; read speech pauses split some segments), 31 decided early, 0 dropped by errors
- **commit latency (n=9): p50 421 ms, p95 443 ms** vs 700 ms target: INSUFFICIENT DATA (n=9 < 20 commits)
- decision latency, commit or drop (n=40; drops skip continuity and send, so this is not the commit target): p50 384 ms, p95 439 ms
- of which endpointing (segmenter hangover, stream time): p50 384 ms, p95 384 ms; compute after TurnEnd (wall): p50 1 ms, p95 55 ms

## Isolation (owner only reaches the model)

Owner false rejection (FR) by segment length; a segment is rejected if no committed turn covers >= 0.3 s (or half) of it.

| segment length | FR |
|---|---|
| < 1 s | n/a |
| 1-2 s | 0.0% (0/1) |
| 2-4 s | 0.0% (0/1) |
| >= 4 s | 60.0% (3/5) |
| all | 42.9% (3/7) |

Non-owner false acceptance (FA; enrolled others + stranger): **0.0% (0/17)** of segments with any audio in a committed turn (pre-roll/tail included).

## Collaboration attribution (3 enrolled + stranger labelled Unknown)

- overall: **70.8% (17/24)** of segments committed with the right label
- enrolled speakers: 75.0% (12/16); stranger -> Unknown: 62.5% (5/8)

Notes: min decision length 1 s, threshold 3.8998 (AS-norm cohort), identification on a thread, MockProvider (no network). Read, clean speech, no added noise; live audio will be harder (see T-023).
