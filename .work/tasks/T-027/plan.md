# T-027 plan
1. `cohort.py`: Cohort(embeddings, embedder, threshold, top_n, score_scale) with npz save/load (embeddings+metadata only, no audio), `build_cohort` calibrating threshold (FAR 1 %, held-out speakers) and score_scale (1/median cohort std), `load_default`.
2. Matcher(cohort=Cohort|array): takes threshold/top_n from the Cohort; exposes `score_scale`.
3. Policies: `score_scale` multiplies early_score_buffer/early_margin/window_buffer/window_drop; Collaboration binds from matcher; Isolation gets it via bind_matcher too.
4. CLI demo loads data/cohort-<embedder>.npz when present; `python -m voiceroom.bench.cohort` builds it. Defaults stay off (live check T-023 pending -> needs_human already exists).
5. Smoke script extension; decisions D-013; summary.
