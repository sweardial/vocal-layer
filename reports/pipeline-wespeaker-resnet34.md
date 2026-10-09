# Pipeline benchmark: wespeaker-resnet34

- dataset: LibriSpeech dev-clean
- config: conversations=14 turns=6 seed=0 speed=3x window_drop=0.4 window_bad_run=3 enroll_utts=5 VAD=SileroVAD
- wall time: 482s

## Latency: end of speech -> commit (isolation pipeline, realtime pacing)

- 84 ground-truth segments -> 131 pipeline turns (36 committed; read speech pauses split some segments), 113 decided early, 0 dropped by errors
- **commit latency (n=36): p50 404 ms, p95 424 ms** vs 700 ms target: MEETS
- decision latency, commit or drop (n=131; drops skip continuity and send, so this is not the commit target): p50 384 ms, p95 429 ms
- of which endpointing (segmenter hangover, stream time): p50 384 ms, p95 384 ms; compute after TurnEnd (wall): p50 0 ms, p95 45 ms

## Isolation (owner only reaches the model)

Owner false rejection (FR) by segment length; a segment is rejected if no committed turn covers >= 0.3 s (or half) of it.

| segment length | FR |
|---|---|
| < 1 s | n/a |
| 1-2 s | 0.0% (0/1) |
| 2-4 s | 0.0% (0/5) |
| >= 4 s | 5.9% (1/17) |
| all | 4.3% (1/23) |

Non-owner false acceptance (FA; enrolled others + stranger): **1.6% (1/61)** of segments with any audio in a committed turn (pre-roll/tail included).

## Collaboration attribution (3 enrolled + stranger labelled Unknown)

- overall: **96.4% (81/84)** of segments committed with the right label
- enrolled speakers: 98.4% (61/62); stranger -> Unknown: 90.9% (20/22)

Notes: min decision length 1 s, threshold 0.35, identification on a thread, MockProvider (no network). Read, clean speech, no added noise; live audio will be harder (see T-023).
