# Speaker-ID bench: campplus-en-voxceleb

- **dataset**: LibriSpeech dev-clean (40 speakers)
- **seed**: 0
- **noise**: white gaussian, SNR measured over speech samples
- **config**: conversations=30 speakers/conv=3 turns=8 enroll_utts=5 conditions=['clean', '10 dB SNR']
- **operating threshold**: 0.376 (configured)

## clean

- trials: 960 target, 1920 non-target

### EER

EER **4.24%** at threshold 0.315.

### FAR / FRR at the operating threshold

Threshold 0.376: FAR 1.04%, FRR 9.17%.

Threshold for a target FAR (calibrated on this condition):

| target FAR | threshold | actual FAR | FRR |
|---|---|---|---|
| 1.00% | 0.376 | 0.99% | 9.17% |
| 0.10% | 0.441 | 0.05% | 14.79% |

### Results by utterance length

FAR/FRR at threshold 0.376.

| length | targets | non-targets | EER | FAR | FRR |
|---|---|---|---|---|---|
| <1 s | 240 | 480 | 8.65% | 0.21% | 34.58% |
| 1-2 s | 484 | 968 | 0.77% | 0.72% | 1.03% |
| 2-4 s | 61 | 122 | 0.00% | 0.82% | 0.00% |
| >=4 s | 175 | 350 | 0.00% | 3.14% | 0.00% |
| all >=1 s | 720 | 1440 | 0.73% | 1.32% | 0.69% |

### Latency (per-clip embedding)

- segments: 960
- p50: 13.6 ms
- p95: 79.0 ms

### Real-time factor

RTF 0.0086 (embedding time / audio duration; < 1 is faster than real time).

## 10 dB SNR

- trials: 960 target, 1920 non-target

### EER

EER **11.25%** at threshold 0.219.

### FAR / FRR at the operating threshold

Threshold 0.376: FAR 1.56%, FRR 31.67%.

Threshold for a target FAR (calibrated on this condition):

| target FAR | threshold | actual FAR | FRR |
|---|---|---|---|
| 1.00% | 0.388 | 0.99% | 33.65% |
| 0.10% | 0.519 | 0.05% | 58.02% |

### Results by utterance length

FAR/FRR at threshold 0.376.

| length | targets | non-targets | EER | FAR | FRR |
|---|---|---|---|---|---|
| <1 s | 240 | 480 | 20.42% | 0.42% | 76.25% |
| 1-2 s | 484 | 968 | 7.02% | 1.45% | 23.97% |
| 2-4 s | 61 | 122 | 1.64% | 1.64% | 1.64% |
| >=4 s | 175 | 350 | 2.29% | 3.43% | 2.29% |
| all >=1 s | 720 | 1440 | 6.25% | 1.94% | 16.81% |

### Latency (per-clip embedding)

- segments: 960
- p50: 14.0 ms
- p95: 91.4 ms

### Real-time factor

RTF 0.0107 (embedding time / audio duration; < 1 is faster than real time).
