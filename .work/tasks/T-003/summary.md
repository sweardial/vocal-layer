# T-003 summary
- Added `voiceroom/embedders/` : `base.py` (Embedder protocol, `embed_batch` helper falling back to loop, `l2_normalize`), `__init__.py` (`get_embedder(name)` lazy registry, `available()`), `toy.py` (numpy log-mel mean/std, tilt-detrended; silence/NaN -> fixed unit vector).
- `scripts/smoke/smoke_embedder_toy.py`: 4 synthetic speakers, same-vs-diff margin 0.264 (>=0.1), unit-norm, deterministic, silence, unknown name.
- Verified: `make check` green. Review (0 HIGH, 2 MEDIUM) -> both fixed (silence unit-norm + smoke assert).
- Unfixed LOW: no sample-rate check in protocol; toy memory on very long clips; margin varies by speaker set (0.107 on seeds 5-8).
