# Scoring variants: noisy / short-utterance comparison

- **dataset**: LibriSpeech dev-clean, seed 0, 30 conversations
- **embedder**: wespeaker-resnet34
- **noise**: test: white gaussian; enrollment augmentation: pink/brown at (20.0, 10.0, 5.0) dB (different noise type on purpose)
- **cohort**: enrollment windows of speakers outside the conversation, AS-norm top-100
- **threshold**: per variant, lowest with clean >= 1 s FAR <= 1.00%

| variant | threshold | clean >=1 s FAR | clean >=1 s FRR | 10 dB >=1 s FAR | **10 dB >=1 s FRR** | 10 dB >=1 s EER | clean <1 s FRR | 10 dB <1 s FRR |
|---|---|---|---|---|---|---|---|---|
| baseline (centroid) | 0.362 | 0.97% | 0.97% | 1.04% | **18.19%** | 5.83% | 24.58% | 72.50% |
| multi-centroid (top-3 chunks) | 0.384 | 0.97% | 0.69% | 1.11% | **17.78%** | 6.81% | 24.58% | 71.25% |
| multi-centroid (3 k-means) | 0.367 | 0.97% | 0.42% | 1.18% | **16.67%** | 6.11% | 22.50% | 67.92% |
| AS-norm | 2.880 | 0.97% | 0.97% | 0.69% | **16.53%** | 5.83% | 22.92% | 60.83% |
| noise-aug enrollment | 0.367 | 0.97% | 0.97% | 1.60% | **14.03%** | 5.69% | 24.58% | 68.75% |
| noise-aug + AS-norm | 2.881 | 0.97% | 1.11% | 0.97% | **12.36%** | 5.42% | 23.33% | 58.33% |
| noise-aug + top-3 chunks | 0.431 | 0.97% | 0.83% | 1.67% | **14.44%** | 6.22% | 28.75% | 68.75% |
| noise-aug + top-3 + AS-norm | 4.327 | 0.97% | 3.75% | 0.76% | **21.11%** | 5.94% | 32.08% | 63.33% |
