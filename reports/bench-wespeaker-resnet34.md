# Speaker-ID bench: wespeaker-resnet34

- **dataset**: LibriSpeech dev-clean (40 speakers)
- **seed**: 0
- **noise**: white gaussian, SNR measured over speech samples
- **config**: conversations=30 speakers/conv=3 turns=8 enroll_utts=5 conditions=['clean', '10 dB SNR']
- **operating threshold**: 0.299 (EER threshold of the first condition)

## clean

- trials: 960 target, 1920 non-target

### EER

EER **3.33%** at threshold 0.299.

### FAR / FRR at the operating threshold

Threshold 0.299: FAR 3.33%, FRR 3.33%.

Threshold for a target FAR (calibrated on this condition):

| target FAR | threshold | actual FAR | FRR |
|---|---|---|---|
| 1.00% | 0.353 | 0.99% | 4.90% |
| 0.10% | 0.407 | 0.05% | 8.54% |

### Results by utterance length

FAR/FRR at threshold 0.299.

| length | targets | non-targets | EER | FAR | FRR |
|---|---|---|---|---|---|
| <1 s | 240 | 480 | 7.08% | 1.46% | 13.33% |
| 1-2 s | 484 | 968 | 0.62% | 3.10% | 0.00% |
| 2-4 s | 61 | 122 | 0.00% | 4.10% | 0.00% |
| >=4 s | 175 | 350 | 0.00% | 6.29% | 0.00% |
| all >=1 s | 720 | 1440 | 0.69% | 3.96% | 0.00% |

### Latency (per-clip embedding)

- segments: 960
- p50: 74.7 ms
- p95: 591.7 ms

### Real-time factor

RTF 0.0601 (embedding time / audio duration; < 1 is faster than real time).

## 10 dB SNR

- trials: 960 target, 1920 non-target

### EER

EER **10.10%** at threshold 0.212.

### FAR / FRR at the operating threshold

Threshold 0.299: FAR 3.49%, FRR 20.73%.

Threshold for a target FAR (calibrated on this condition):

| target FAR | threshold | actual FAR | FRR |
|---|---|---|---|
| 1.00% | 0.374 | 0.99% | 31.35% |
| 0.10% | 0.442 | 0.05% | 44.90% |

### Results by utterance length

FAR/FRR at threshold 0.299.

| length | targets | non-targets | EER | FAR | FRR |
|---|---|---|---|---|---|
| <1 s | 240 | 480 | 17.92% | 1.88% | 55.83% |
| 1-2 s | 484 | 968 | 7.23% | 3.10% | 13.22% |
| 2-4 s | 61 | 122 | 1.23% | 4.10% | 0.00% |
| >=4 s | 175 | 350 | 2.29% | 6.57% | 0.57% |
| all >=1 s | 720 | 1440 | 6.67% | 4.03% | 9.03% |

### Latency (per-clip embedding)

- segments: 960
- p50: 76.5 ms
- p95: 596.0 ms

### Real-time factor

RTF 0.0601 (embedding time / audio duration; < 1 is faster than real time).
