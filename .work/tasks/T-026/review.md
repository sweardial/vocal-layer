# Review T-026: Evaluate CAM++ as an embedder candidate

Verified by the reviewer:
- `make check` is green: lint is clean and 23/23 smoke scripts pass. `smoke_embedder_campplus.py` ran for real and did not SKIP, in 1.35 s. The suite total was 75.9 s. That is already over the policy's 60 s figure, but the overrun existed before this task and T-030 owns it.
- The model on disk matches the pinned sha256 `357a834f...129b`. The ONNX metadata (`normalize_samples=1`, `feature_normalize_type=global-mean`, input `x [N,T,80]`, 512-d output) matches the front end in `campplus.py`. Samples in [-1,1], povey window, and per-utterance mean norm are all correct for 3D-Speaker.
- Edge inputs give finite unit vectors: empty, 1 sample, 1 s of zeros, and over-range amplitude. The behaviour matches the wespeaker embedder.
- D-012 numbers match both reports. They are clean EER 4.24 vs 3.33, 10 dB EER 11.25 vs 10.10, >=1 s FAR/FRR 1.32/0.69 vs 1.18/0.28, and 10 dB FRR 16.81 vs 16.94. The "not adopted" conclusion follows from the data, and `DEFAULT_EMBEDDER` is unchanged.
- No audio or embeddings are logged; the smoke prints only cosine scores. No pickle or torch.load is used, and the ONNX file is sha-pinned. Nothing provider-specific was added. The store tags signatures with the embedder name, so switching to the 512-d model can't silently mix signatures.
- `.work/tasks/T-026/` had no `plan.md` or `summary.md`, so this diff could not be checked against a plan.

## Findings

[LOW] src/voiceroom/bench/__main__.py:14,29: `campplus-en-voxceleb` was added to `REAL_EMBEDDERS`, and under `--embedder all` a `FileNotFoundError` is fatal, not a skip. → Any checkout whose `models/` came from before this change will fail `make bench` with exit 2 ("embedder 'campplus-en-voxceleb' unavailable"). The failure comes from a candidate that D-012 explicitly did not adopt. Every later bench run also pays for embedding the full corpus twice more. → Either keep non-adopted candidates out of `all` (bench them with an explicit `--embedder campplus-en-voxceleb`) or let `all` skip missing non-default model files. Alternatively, keep the current behaviour and note in D-012 that `fetch_models.py` must be re-run.

[LOW] reports/bench-wespeaker-resnet34.md (unchanged) / reports/bench-ecapa-voxceleb.md:43-91 / .work/decisions.md D-012: `make bench` was not run as one invocation. The wespeaker report was not regenerated (its mtime is from T-025), while the ecapa report was, and the pipeline bench step left no trace. → D-012's latency claim ("p95 ~83 ms vs wespeaker ~164 ms") compares runs from different sessions under different machine load. The ecapa p95 moved by 38 ms between runs, which shows load varies. The acceptance item "`make bench` green" is also unverified. The ecapa report churn is unrelated noise in this diff. → Run `make bench` once, in full, and commit all three reports from that run (or revert the ecapa churn). Then restate the latency ratio from that single run.

Counts: HIGH 0, MEDIUM 0, LOW 2.
