# T-040 summary
- `policy.TrackPolicy(mode="verified"|"shared", members, claimed, unknown, on_mismatch)`: verified = 1:1 check vs claimed signature at `verify_threshold` (id threshold - `VERIFY_THRESHOLD_RELAX`, config.py, uncalibrated, points at T-042); confident match to another member -> relabel/drop (`TRACK_ON_MISMATCH`); else unknown policy; lenient (short) continuity always. A confident non-member never accepts as owner (reviewer M1). shared = CollaborationPolicy. Added to `room.Policy`.
- `scripts/smoke/smoke_room_identity.py`: owner 1-2 s turns, other member drop/relabel, stranger drop/Unknown, shared mic with 2 members, plus policy-level check of the relaxed threshold and default bind (cosine and z-scale).
- Verified: `make check` green (28 scripts).
- Known: relabelled turns also get lenient continuity; owner without a loaded signature is muted; non-member claimed identity raises in `TrackPolicy`. Smoke does not prove lenient-vs-strict continuity (toy embedder).
