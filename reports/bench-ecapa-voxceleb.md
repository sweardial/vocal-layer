# Speaker-ID bench: ecapa-voxceleb

- **dataset**: LibriSpeech dev-clean (40 speakers)
- **seed**: 0
- **noise**: white gaussian, SNR measured over speech samples
- **config**: conversations=30 speakers/conv=3 turns=8 enroll_utts=5 conditions=['clean', '10 dB SNR']
- **operating threshold**: 0.248 (EER threshold of the first condition)

## clean

- trials: 960 target, 1920 non-target

### EER

EER **4.71%** at threshold 0.248.

### FAR / FRR at the operating threshold

Threshold 0.248: FAR 4.74%, FRR 4.69%.

Threshold for a target FAR (calibrated on this condition):

| target FAR | threshold | actual FAR | FRR |
|---|---|---|---|
| 1.00% | 0.319 | 0.99% | 10.21% |
| 0.10% | 0.420 | 0.05% | 20.62% |

### Results by utterance length

FAR/FRR at threshold 0.248.

| length | targets | non-targets | EER | FAR | FRR |
|---|---|---|---|---|---|
| <1 s | 240 | 480 | 12.08% | 2.71% | 18.33% |
| 1-2 s | 484 | 968 | 1.45% | 4.44% | 0.21% |
| 2-4 s | 61 | 122 | 0.00% | 5.74% | 0.00% |
| >=4 s | 175 | 350 | 0.00% | 8.00% | 0.00% |
| all >=1 s | 720 | 1440 | 1.11% | 5.42% | 0.14% |

### Latency (per-clip embedding)

- segments: 960
- p50: 82.9 ms
- p95: 509.9 ms

### Real-time factor

RTF 0.0559 (embedding time / audio duration; < 1 is faster than real time).

## 10 dB SNR

- trials: 960 target, 1920 non-target

### EER

EER **11.46%** at threshold 0.172.

### FAR / FRR at the operating threshold

Threshold 0.248: FAR 3.49%, FRR 20.52%.

Threshold for a target FAR (calibrated on this condition):

| target FAR | threshold | actual FAR | FRR |
|---|---|---|---|
| 1.00% | 0.317 | 0.99% | 32.08% |
| 0.10% | 0.409 | 0.05% | 49.48% |

### Results by utterance length

FAR/FRR at threshold 0.248.

| length | targets | non-targets | EER | FAR | FRR |
|---|---|---|---|---|---|
| <1 s | 240 | 480 | 23.33% | 1.67% | 57.92% |
| 1-2 s | 484 | 968 | 7.44% | 3.72% | 11.98% |
| 2-4 s | 61 | 122 | 0.00% | 3.28% | 0.00% |
| >=4 s | 175 | 350 | 1.71% | 5.43% | 0.00% |
| all >=1 s | 720 | 1440 | 6.11% | 4.10% | 8.06% |

### Latency (per-clip embedding)

- segments: 960
- p50: 81.4 ms
- p95: 510.6 ms

### Real-time factor

RTF 0.0559 (embedding time / audio duration; < 1 is faster than real time).
