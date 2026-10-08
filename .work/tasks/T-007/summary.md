# T-007 summary

## Changed
- `scripts/fetch_data.py`: dev-clean (or `--mini` = dev-clean-2). Mirror fallback, Range resume of `data/downloads/*.part`, short-read detection, md5 check (dev-clean md5 pinned; otherwise md5sum.txt from mirrors), `filter="data"` tar extraction into a temp dir then rename, `.complete` sentinel for skip-if-present.
- `src/voiceroom/eval/corpus.py`: `Utterance`, `Corpus` protocol, `LibriSpeechCorpus(root, subset=None)`, `SynthCorpus(n_speakers, seed)` (profile seeds derived from the seed RNG).
- `scripts/smoke/smoke_corpus.py`: always checks SynthCorpus; LibriSpeech part (>= 40 speakers) runs only if `data/LibriSpeech/dev-clean` exists, else prints a skip note for that part and still passes.

## Verification
- `uv run python scripts/fetch_data.py` run in the container: md5 42e2234ba48799c1f50f24a7926300a1 verified, extracted.
- Index counts: **40 speakers, 2703 utterances, 5.39 h** (dev-clean).
- Re-run prints "already present"; deleting the sentinel re-extracted from the cached archive.
- `make check`: lint clean, 7/7 smoke PASS (~10 s).
- Reviewer: 0 HIGH, 3 MEDIUM (partial-extract skip, short-read resume, runtime-only md5) and 2 LOW, all fixed.

## Notes / follow-ups
- Download is slow (~0.2 MB/s from openslr.trmal.net, ~50 min). Running two fetches at once corrupts the `.part`; no lock.
- The `--mini` md5 is not pinned (fetched from md5sum.txt); `--mini` was not downloaded.
