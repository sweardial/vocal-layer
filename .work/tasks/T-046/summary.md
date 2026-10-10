# T-046 summary
Added `src/voiceroom/sessions.py`: `SessionStore` (Fernet-encrypted session files, create/invite/accept/revoke/list/join_token), HMAC-signed expiring single-use invite codes (enrolled + consent required at invite and accept), `SessionPolicy` (isolation = owner only; collaboration = verified members, others unknown; follows live membership) and `SessionSync` (drops + kicks revoked members; retries failed kicks). Also: `RoomPipeline.deliver_gate` (queued turns of revoked members are not forwarded), `FakeRoom.kick`, `livekit_room.make_kicker` (RoomService) and token TTL (join tokens 1 h).
Verified: `scripts/smoke/smoke_sessions.py` (create->invite->accept->forwarded; expired/reused/tampered/non-enrolled rejected; revoke mid-session; isolation owner-only; no code logged). `make check` green (34 scripts, 3 runs). Reviewer HIGH/MEDIUM fixed (stale cache, stranger kicks via `revoked` list, kick retry, token TTL).
Limits / follow-ups: a revoked member connected without sending audio is not kicked (SessionSync only sees pipeline sources) until their token expires; join_token and make_kicker are not exercised offline here (livekit extra not installed; no smoke against the local server).

## Round 2 (independent reviewer FAIL)
- HIGH: `SessionSync(..., agent=RoomAgent)` now syncs against the transport roster (`RoomAgent.participants`, new), so a revoked member connected without audio is kicked on revoke and on join (the 0.5 s sync loop).
- MEDIUM: delivery gate denies only revoked claims; a never-member shared device is allowed when the recognized label is a current member.
- MEDIUM: kick uses the transport identity (`ParticipantJoined.identity`), not the `voiceroom_user` claim; `FakeRoom.kicked_identities` records it.
- Verified: `smoke_sessions.py` `roster_check` (listening-only participant, identity `device-b` with `voiceroom_user=bob`, shared-device gate); `make check` green twice (34 scripts, 0 failed).
- Remaining: the LiveKit side (`LiveKitRoom` roster, `make_kicker`) is still not run against the local server.
