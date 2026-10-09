# T-034 summary
- Cohort is now a plain npz (embeddings + threshold/scale, no audio, no personal data): `Cohort.save(path)`, `Cohort.load(path)`, `load_default(embedder, directory)` no longer take a key. Legacy encrypted files give a clear "rebuild it" error.
- Updated callers (cli, bench/cohort, bench/pipeline), smokes (`smoke_e2e_cohort`, `smoke_noise_robust`: plain npz, legacy file rejected), docs (dropped `--no-cohort` note/flags).
- Converted local `data/cohort-wespeaker-resnet34.npz` in place (decrypted with the local key); loads, threshold 3.90.
- Verified: `make check` green (26 scripts, 0 failed).
- Follow-ups: none. Other machines need the converted file (data/ is gitignored) or `python -m voiceroom.bench.cohort`.

## Fix round 2 (integrity of the unencrypted cohort)
- `config.COHORT_SHA256` pins the trusted SHA-256 per embedder. `Cohort.load(path, sha256)` refuses any file whose digest differs (covers embeddings and the threshold/score_scale); `load_default` uses the pin (or an explicit `sha256`) and refuses an existing but unpinned file. `Cohort.save` returns the digest; `bench.cohort` prints it for pasting into config. Kept the 0–20 range check as defence in depth.
- `smoke_e2e_cohort` now builds a copy with threshold=0 and asserts it is rejected, and that an unpinned file is refused.
- Verified: `make check` green (26 scripts, 0 failed); real `data/` cohort loads via the pin (threshold 3.90).
- Note: rebuilding the cohort requires updating the pin in config (printed by the builder); the demo refuses a mismatching file with the "bad cohort file" error.
