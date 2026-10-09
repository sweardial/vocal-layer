# T-011 summary

## Changed
- `python -m voiceroom.bench` (`bench/__main__.py`): `--embedder NAME|all` (`all` = wespeaker, plus ecapa if its extra/checkpoint is present), `--corpus synth|librispeech`, `--n-conversations` (default 30), `--snr` (noisy conditions, default 10 dB; a clean run is always included), `--seed`. Missing LibriSpeech (or too few speakers) exits 2 with a `scripts/fetch_data.py` hint; a missing model exits 2 with a `scripts/fetch_models.py` hint. `all` skips ECAPA only when the package isn't installed.
- `bench/report.py`: one report per embedder, one section per condition (clean, 10 dB SNR). The operating threshold is the clean EER threshold, so the noisy sections show degradation at that threshold. Target-FAR rows say n/a when there are too few non-targets. The header states the noise type (white Gaussian).
- `bench/run.py`: each conversation draws a seeded random speaker set (previously a fixed stride that used each speaker once).
- `Makefile`: `make bench` runs `uv run $(BENCH_UV) python -m voiceroom.bench --corpus librispeech --embedder all`, with `BENCH_UV ?= --extra ecapa` (use `make bench BENCH_UV=` for wespeaker only).
- `smoke_bench.py`: now covers the clean + noisy sections and the missing-data exit code (2).
- Committed `reports/bench-wespeaker-resnet34.md` and `reports/bench-ecapa-voxceleb.md`.

## Results (LibriSpeech dev-clean, 30 conversations x 3 speakers, 960 target / 1920 non-target trials)
| embedder | clean EER | 10 dB EER | RTF |
|---|---|---|---|
| wespeaker-resnet34 | 3.33 % | 10.10 % | 0.060 |
| ecapa-voxceleb | 4.71 % | 11.46 % | 0.056 |

## Runtime
`make bench` took 29m58s wall in the container (wespeaker 933 s, ecapa 860 s; each covers clean + 10 dB). Use `--n-conversations 8` for a quick run.

## Verification
- `make bench` -> both reports written; `make check` green (10 smoke scripts, ~22 s).
- `python -m voiceroom.bench --corpus librispeech --data /nonexistent --embedder all` -> message + exit 2.
- Reviewer: no HIGH; the 4 MEDIUM findings were fixed (speaker sampling, smoke timing/coverage, summary). The Makefile still passes `--extra ecapa` by default (overridable); it needs the extra already synced if offline.

## Follow-ups / caveats
- Latency p50/p95 pooled over full utterances and short crops, so p95 (~0.6 s) overstates the per-turn latency; T-012 should read the by-length data with care. Per-bucket latency was not added.
