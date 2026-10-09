# Speaker-ID bench: ecapa-voxceleb

- **dataset**: LibriSpeech dev-clean (40 speakers)
- **seed**: 0
- **noise**: white gaussian, SNR measured over speech samples
- **config**: conversations=30 speakers/conv=3 turns=8 enroll_utts=5 conditions=['clean', '10 dB SNR']
- **operating threshold**: 0.320 (configured)

## clean

- trials: 960 target, 1920 non-target

### EER

EER **4.71%** at threshold 0.248.

### FAR / FRR at the operating threshold

Threshold 0.320: FAR 0.99%, FRR 10.31%.

Threshold for a target FAR (calibrated on this condition):

| target FAR | threshold | actual FAR | FRR |
|---|---|---|---|
| 1.00% | 0.319 | 0.99% | 10.21% |
| 0.10% | 0.420 | 0.05% | 20.62% |

### Results by utterance length

FAR/FRR at threshold 0.320.

| length | targets | non-targets | EER | FAR | FRR |
|---|---|---|---|---|---|
| <1 s | 240 | 480 | 12.08% | 0.21% | 37.92% |
| 1-2 s | 484 | 968 | 1.45% | 0.93% | 1.65% |
| 2-4 s | 61 | 122 | 0.00% | 1.64% | 0.00% |
| >=4 s | 175 | 350 | 0.00% | 2.00% | 0.00% |
| all >=1 s | 720 | 1440 | 1.11% | 1.25% | 1.11% |

### Latency (per-clip embedding)

- segments: 960
- p50: 86.6 ms
- p95: 500.1 ms

### Real-time factor

RTF 0.0556 (embedding time / audio duration; < 1 is faster than real time).

## 10 dB SNR

- trials: 960 target, 1920 non-target

### EER

EER **11.46%** at threshold 0.172.

### FAR / FRR at the operating threshold

Threshold 0.320: FAR 0.94%, FRR 32.50%.

Threshold for a target FAR (calibrated on this condition):

| target FAR | threshold | actual FAR | FRR |
|---|---|---|---|
| 1.00% | 0.317 | 0.99% | 32.08% |
| 0.10% | 0.409 | 0.05% | 49.48% |

### Results by utterance length

FAR/FRR at threshold 0.320.

| length | targets | non-targets | EER | FAR | FRR |
|---|---|---|---|---|---|
| <1 s | 240 | 480 | 23.33% | 0.21% | 77.08% |
| 1-2 s | 484 | 968 | 7.44% | 0.83% | 25.62% |
| 2-4 s | 61 | 122 | 0.00% | 0.82% | 0.00% |
| >=4 s | 175 | 350 | 1.71% | 2.29% | 1.71% |
| all >=1 s | 720 | 1440 | 6.11% | 1.18% | 17.64% |

### Latency (per-clip embedding)

- segments: 960
- p50: 82.5 ms
- p95: 506.7 ms

### Real-time factor

RTF 0.0558 (embedding time / audio duration; < 1 is faster than real time).
