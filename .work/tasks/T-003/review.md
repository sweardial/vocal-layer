# Review T-003: embedder protocol, registry, toy embedder

`make check` passed (ruff clean; smoke_embedder_toy PASS, margin=0.264). No `plan.md` exists for T-003.
Scope is fine. There is no provider code outside adapters, no logging of audio or embeddings, and no unsafe deserialization.

## Findings

[MEDIUM] src/voiceroom/embedders/toy.py:59 (with base.py:31): silent, empty, or all-zero input returns an all-zero vector (norm 0.0). This breaks the documented "unit-norm" contract in base.py:14. → I checked `embed(np.zeros(16000))` and `embed(np.zeros(0))`, and both return norm 0.0. In T-004, enrollment averages chunk embeddings into a centroid. A silent 2 s pause between enrollment phrases adds a zero vector, which shrinks the centroid. Every later score against that signature then drops by about the fraction of silent chunks, so real users can fall below the threshold. A matcher that re-normalises with `v/‖v‖` gets NaN. → Make the degenerate case explicit. Either raise `ValueError("no speech")` or return a sentinel the caller must check. Document it in the `Embedder` protocol so other backends behave the same way.

[MEDIUM] scripts/smoke/smoke_embedder_toy.py:37: the silence check only asserts `isfinite`. It passes on the zero-vector bug above, so the unit-norm claim is never actually tested on degenerate input. → Assert the chosen contract instead (raises, or unit-norm).

[LOW] src/voiceroom/embedders/toy.py:51-58: NaN or inf input gives an all-NaN embedding with no error. Only numpy RuntimeWarnings ("Mean of empty slice") appear, because `energy > energy.max() - 8` is all False. → If a corrupt or failed resample chunk produces NaN, a NaN chunk embedding makes the whole enrollment centroid NaN. Every later `identify` against it then returns NaN scores, and `np.argmax` silently picks index 0. → Check `np.isfinite(x).all()` up front and raise ValueError. Also guard `keep.any()`.

[LOW] src/voiceroom/embedders/base.py:13: the protocol has no sample-rate argument or check. The only guard is the parameter name `audio16k`. → Realtime providers commonly stream 24 kHz PCM. If an adapter passes those frames straight to `embed`, the mel mapping is wrong and nothing errors, so speakers are silently misidentified. → Add an `sr: int = 16000` parameter that raises when `sr != 16000`, or a validated `Audio16k` wrapper used at the adapter boundary.

[LOW] scripts/smoke/smoke_embedder_toy.py:16,32: the margin assertion depends on the speakers you pick. I re-ran the same test on other speaker sets: speakers 5-8 gave a margin of 0.107 (only 0.007 above the 0.1 bar), and 10-13 gave 0.160. Different-speaker cosines reach 0.95. Same-speaker cosine is about 1.000 because synth seeds barely change the voice. → A small change to `synth` (T-008 noise, new profiles) could make this smoke flaky with no regression in the embedder. The near-1.0 different-speaker cosines will also force very tight thresholds in T-004. → Average over more speakers (e.g. 8) and/or assert that min(same) > max(diff). Note the toy cosine range in summary.md so T-004 can calibrate.

[LOW] src/voiceroom/embedders/toy.py:47-48: the whole framed matrix is materialised in float64 (idx int64 + frames + complex rfft). That is about 12 bytes/sample x 512/160 x 3 arrays. → Embedding a 10-minute conversation, as a bench or diarization pass might, allocates about 0.7 GB per call and can OOM a small container. → Process frames in fixed-size blocks, or document a max input length.
