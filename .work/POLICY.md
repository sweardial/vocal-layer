# Testing policy (set by the human, managed by the harness)

Unit tests: OFF (the human decides when to turn them on; the product is still exploratory and tests
would lock in implementations that are likely to change).
- Do NOT write unit tests, pytest files, or test fixtures. Don't plan or request them in reviews.
- Prove each change works with a coarse runnable check: add or update a smoke script in scripts/smoke/
  (one per feature, e.g. run the pipeline on a synthetic conversation and assert the outcome). `make smoke` runs them all.
- Keep smoke scripts few, fast (< 60 s total), offline, and freely rewritable when the design changes.
