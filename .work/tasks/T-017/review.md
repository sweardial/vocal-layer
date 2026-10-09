# Review T-017: collaboration mode

`make check`: green (18 scripts, 0 failed, 59.34 s total; smoke_e2e_collab.py 3.90 s).
Unit tests are OFF per POLICY.md, so none are requested.

No HIGH findings. I found no privacy leak in "drop" mode: an unknown turn never gets ACCEPT
(`decide` rejects it when `final=True` and returns UNDECIDED when `final=False`), and merged
enrolled+stranger turns still go through the per-label continuity floor. Nothing logs audio or
embeddings, and nothing is deserialized.

## MEDIUM

[MEDIUM] src/voiceroom/policy.py:114 (and :117 window_drop): `CollaborationPolicy` gets its
threshold and window_drop from `embedder=""`, which falls back to FALLBACK_THRESHOLD 0.35 and
FALLBACK_WINDOW_DROP 0.012. The Matcher's actual threshold is never consulted. →
`CollaborationPolicy(["alice","bob","carol"])`, the exact signature in the backlog, combined with
a wespeaker Matcher uses the toy-calibrated window drop of 0.012 instead of 0.05. Natural
window-to-window variation on a real embedder then fails `windows_ok`, so many correctly
identified enrolled turns get REJECTed and are silently lost. With an AS-norm Matcher (threshold
2.88 z-units), the 0.35+0.1 cosine early bar is cleared by almost any enrolled match. That causes
early ACCEPTs on weak evidence, and the window floor becomes meaningless. → Pass the matcher's
threshold and embedder name explicitly: either make them required, or have Pipeline check that
`policy.threshold == matcher.threshold` at construction. Do not default silently.

[MEDIUM] src/voiceroom/pipeline.py:305-308 with policy.py:148: in collaboration mode, a failed
continuity check turns the whole turn into REJECT. Nothing falls back to a full-turn decision.
→ Enrolled A says "yeah" and enrolled B continues without a pause long enough to split the turn.
The early match labels it A, then B's windows fail `m.best == "A"`, so the turn is dropped and B's
question never reaches the model. Isolation mode needs this fail-closed behavior. In
collaboration mode it throws away speech from people who are allowed to be forwarded. The same
happens when an early ACCEPT was simply wrong: the full-turn identification would have given the
right label, but it never runs. → When `windows_ok` fails after an early decision, re-decide with
`final=True` on the full turn and re-check continuity. If two enrolled speakers are both present,
consider forwarding the turn with the full-turn label (or splitting it). Drop it only when a
non-enrolled window is present and `unknown == "drop"`.

[MEDIUM] scripts/smoke/smoke_e2e_collab.py:72/87: the acceptance requires unknown turns to be
"labeled 'Unknown' under 'label'". The script only asserts that at least one turn is labeled
Unknown, and in label mode a REJECTed unknown turn is never added to `total`. → If label mode
dropped 9 of 10 stranger turns (for example because `windows_ok`'s Unknown branch at
policy.py:142 rejects any turn where one short window happens to match an enrolled speaker), the
smoke still passes. → In label mode, count rejected stranger turns as wrong (`total += 1`), or
assert that the fraction of stranger turns committed as Unknown is >= 95 %.

## LOW

[LOW] scripts/smoke/smoke_e2e_collab.py:22-32: the threshold is calibrated on `held`
utterances. `make_conversation(..., n_enroll=6)` builds the test conversations from those same
utterances. → The ≥ 95 % accuracy assertion runs at an operating point fitted to the test data,
so it overstates real accuracy and can hide a regression in the policy's thresholds. → Calibrate
on a disjoint split (for example a different corpus seed or speakers), or use the configured
threshold.

[LOW] scripts/smoke/smoke_e2e_collab.py (suite budget): `make smoke` now takes 59.34 s against
POLICY.md's "< 60 s total". → On a slightly slower or busier machine, or after the next smoke
script lands, the suite exceeds the budget. → Reduce the work here: one seed per mode, or reuse
one conversation for both modes. Alternatively, trim smoke_identify_isolation.py (15 s).
