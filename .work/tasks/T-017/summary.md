# T-017 summary
- `policy.py`: `CollaborationPolicy(enrolled, unknown="drop"|"label", ...)`; enrolled match -> ACCEPT with name, unknown -> REJECT or ACCEPT "Unknown"; early decision only for confident enrolled matches; label-aware continuity (`windows_ok(matches, label)`). Threshold/window_drop are bound from the matcher by `Pipeline` (`bind_matcher`) when unset.
- `pipeline.py`: accepts either policy; passes decision label to the continuity check (label already went to `send_turn`).
- `scripts/smoke/smoke_e2e_collab.py`: 3 enrolled + 1 unknown, both modes; label accuracy 100% (>=95%), drop/label behaviour asserted, dropped should-be-forwarded turns count as wrong.
- Verified: `make check` green (58.3 s, tight vs 60 s budget).
- Review: 3 MEDIUM; fixed #1 (defaults) and #3 (smoke strength). #2 left: a turn whose continuity check fails is dropped whole (e.g. "yeah" by A then B continues without pause) -> follow-up T-028.
