# Pipeline benchmark: wespeaker-resnet34

- dataset: LibriSpeech dev-clean
- config: conversations=4 turns=6 seed=0 speed=1x window_drop=config enroll_utts=5 VAD=SileroVAD
- wall time: 538s

## Latency: end of speech -> commit (isolation pipeline, realtime pacing)

- 24 ground-truth segments -> 39 pipeline turns (1 committed; read speech pauses split some segments), 24 decided early, 0 dropped by errors
- **commit latency (n=1): p50 489 ms, p95 489 ms** vs 700 ms target: INSUFFICIENT DATA (n=1 < 20 commits)
- decision latency, commit or drop (n=39; drops skip continuity and send, so this is not the commit target): p50 384 ms, p95 493 ms
- of which endpointing (segmenter hangover, stream time): p50 384 ms, p95 384 ms; compute after TurnEnd (wall): p50 0 ms, p95 109 ms

## Isolation (owner only reaches the model)

Owner false rejection (FR) by segment length; a segment is rejected if no committed turn covers >= 0.3 s (or half) of it.

| segment length | FR |
|---|---|
| < 1 s | n/a |
| 1-2 s | 100.0% (1/1) |
| 2-4 s | n/a |
| >= 4 s | 83.3% (5/6) |
| all | 85.7% (6/7) |

Non-owner false acceptance (FA; enrolled others + stranger): **0.0% (0/17)** of segments with any audio in a committed turn (pre-roll/tail included).

## Collaboration attribution (3 enrolled + stranger labelled Unknown)

- overall: **25.0% (6/24)** of segments committed with the right label
- enrolled speakers: 6.2% (1/16); stranger -> Unknown: 62.5% (5/8)

Notes: min decision length 1 s, threshold 0.35, identification on a thread, MockProvider (no network). Read, clean speech, no added noise; live audio will be harder (see T-023).
