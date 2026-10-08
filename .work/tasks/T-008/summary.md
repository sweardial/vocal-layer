# T-008 summary
Added `eval/conversations.py` (split_utterances, make_conversation, save/load wav+json) and `scripts/smoke/smoke_conversations.py`.
- Enrollment/test split is seeded and disjoint; conversations use test utterances only; no same speaker twice in a row.
- Noise is white Gaussian at SNR measured over speech only (reviewer MEDIUM, fixed). Save/load appends .wav/.json (dotted names safe); sample-rate check raises.
- Verified: `make check` green (8 smoke scripts).
Follow-ups/known: utterances are sampled with replacement, so long conversations can repeat a clip.
