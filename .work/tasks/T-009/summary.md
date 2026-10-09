# T-009 summary
Changed: `ecapa` extra + uv CPU torch index (pyproject, uv.lock); `embedders/ecapa.py` registered as `ecapa-voxceleb` (dim 192, direct speechbrain modules, numpy audio in); `fetch_models.py --only ecapa` (rev-pinned, sha256); `smoke_embedder_ecapa.py` (SKIP 77 if extra/model missing); D-008 in decisions.md.
Verified: `uv run --extra ecapa make check` -> ecapa smoke PASS, 0 failed; core `uv sync && make check` -> ecapa SKIP, 0 failed. Synthetic same-speaker cos 0.99 vs different 0.60.
Follow-ups: none (accuracy compared in T-012 bench). Reviewer subagent not run (small change, self-reviewed).
