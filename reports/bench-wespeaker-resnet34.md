# Speaker-ID bench: wespeaker-resnet34

- **dataset**: LibriSpeech dev-clean (40 speakers)
- **seed**: 0
- **noise**: white gaussian, SNR measured over speech samples
- **config**: conversations=30 speakers/conv=3 turns=8 enroll_utts=5 conditions=['clean', '10 dB SNR']
- **operating threshold**: 0.350 (configured)

## clean

- trials: 960 target, 1920 non-target

### EER

EER **3.33%** at threshold 0.299.

### FAR / FRR at the operating threshold

Threshold 0.350: FAR 0.99%, FRR 4.90%.

Threshold for a target FAR (calibrated on this condition):

| target FAR | threshold | actual FAR | FRR |
|---|---|---|---|
| 1.00% | 0.353 | 0.99% | 4.90% |
| 0.10% | 0.407 | 0.05% | 8.54% |

### Results by utterance length

FAR/FRR at threshold 0.350.

| length | targets | non-targets | EER | FAR | FRR |
|---|---|---|---|---|---|
| <1 s | 240 | 480 | 7.08% | 0.42% | 18.75% |
| 1-2 s | 484 | 968 | 0.62% | 0.83% | 0.41% |
| 2-4 s | 61 | 122 | 0.00% | 1.64% | 0.00% |
| >=4 s | 175 | 350 | 0.00% | 2.00% | 0.00% |
| all >=1 s | 720 | 1440 | 0.69% | 1.18% | 0.28% |

### Latency (per-clip embedding)

- segments: 960
- p50: 24.4 ms
- p95: 163.9 ms

### Real-time factor

RTF 0.0172 (embedding time / audio duration; < 1 is faster than real time).

## 10 dB SNR

- trials: 960 target, 1920 non-target

### EER

EER **10.10%** at threshold 0.212.

### FAR / FRR at the operating threshold

Threshold 0.350: FAR 1.46%, FRR 28.44%.

Threshold for a target FAR (calibrated on this condition):

| target FAR | threshold | actual FAR | FRR |
|---|---|---|---|
| 1.00% | 0.374 | 0.99% | 31.35% |
| 0.10% | 0.442 | 0.05% | 44.90% |

### Results by utterance length

FAR/FRR at threshold 0.350.

| length | targets | non-targets | EER | FAR | FRR |
|---|---|---|---|---|---|
| <1 s | 240 | 480 | 17.92% | 0.21% | 62.92% |
| 1-2 s | 484 | 968 | 7.23% | 1.34% | 24.79% |
| 2-4 s | 61 | 122 | 1.23% | 1.64% | 0.00% |
| >=4 s | 175 | 350 | 2.29% | 3.43% | 1.14% |
| all >=1 s | 720 | 1440 | 6.67% | 1.88% | 16.94% |

### Latency (per-clip embedding)

- segments: 960
- p50: 25.7 ms
- p95: 163.8 ms

### Real-time factor

RTF 0.0172 (embedding time / audio duration; < 1 is faster than real time).
