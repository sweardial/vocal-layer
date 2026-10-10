"""Sessions and invites. A session has an owner, a mode (isolation | collaboration), members and
a room name; membership drives who the room agent accepts (`SessionPolicy`, `SessionSync`).

Sessions are Fernet-encrypted like signatures (one file per session, keyed-hash filename).
An invite code is `vr1.<payload>.<mac>`: an HMAC-SHA256 (subkey of the store key) over the
session id, invitee, expiry and a nonce. It is single-use: the nonce is pending in the session
record until `accept` consumes it. Codes and tokens are never logged or put in error messages.
"""

import asyncio
import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from voiceroom.config import threshold_for
from voiceroom.policy import UNKNOWN_LABEL, IsolationPolicy, TrackPolicy
from voiceroom.rooms.runner import IDENTITY_ATTRIBUTE
from voiceroom.store import SignatureStore, StoreError, StoreKeyError

log = logging.getLogger(__name__)

MODES = ("isolation", "collaboration")
INVITE_TTL_S = 24 * 3600.0
TOKEN_TTL_S = 3600.0  # a revoked member's old token stops working soon
SYNC_INTERVAL_S = 0.5
KICK_TIMEOUT_S = 5.0
_SUFFIX = ".session"
_PREFIX = "vr1"


class SessionError(Exception):
    pass


class InviteError(SessionError):
    """The code is malformed, tampered, expired, already used or no longer valid."""


@dataclass(frozen=True)
class Session:
    id: str
    owner: str
    mode: str
    room: str
    unknown: str = "drop"
    members: tuple[str, ...] = ()  # includes the owner
    revoked: tuple[str, ...] = ()  # removed members: kicked from the room on sight
    pending: dict = field(default_factory=dict, compare=False)  # nonce -> {user, exp}

    def __repr__(self) -> str:
        return f"Session(id={self.id!r}, mode={self.mode!r}, members={len(self.members)})"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class SessionStore:
    def __init__(
        self,
        root: Path | str,
        key: bytes | str,
        signatures: SignatureStore | None = None,
        now: Callable[[], float] = time.time,
    ):
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as e:
            raise StoreKeyError("invalid key: expected a 32-byte urlsafe-base64 Fernet key") from e
        self._key = key.encode() if isinstance(key, str) else bytes(key)
        self._mac_key = hmac.new(self._key, b"voiceroom-invite-v1", hashlib.sha256).digest()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.signatures = signatures or SignatureStore(self.root, key)
        self.now = now
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[tuple[int, int, int], Session]] = {}

    def __repr__(self) -> str:
        return f"SessionStore(root={str(self.root)!r})"

    # ---- persistence ---------------------------------------------------------------------

    def _path(self, sid: str) -> Path:
        digest = hmac.new(self._key, b"session:" + sid.encode(), hashlib.sha256).hexdigest()
        return self.root / (digest + _SUFFIX)

    @contextlib.contextmanager
    def _locked(self):
        with self._lock, open(self.root / ".sessions.lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def _decode(self, raw: bytes) -> Session:
        try:
            d = json.loads(self._fernet.decrypt(raw))
        except InvalidToken as e:
            raise StoreKeyError("cannot decrypt session: wrong key or corrupted file") from e
        return Session(
            d["id"], d["owner"], d["mode"], d["room"], d["unknown"],
            tuple(d["members"]), tuple(d.get("revoked", ())), dict(d["pending"]),
        )  # fmt: skip

    def _write(self, s: Session) -> None:
        rec = {
            "id": s.id, "owner": s.owner, "mode": s.mode, "room": s.room,
            "unknown": s.unknown, "members": list(s.members), "pending": s.pending,
            "revoked": list(s.revoked),
        }  # fmt: skip
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(self._fernet.encrypt(json.dumps(rec).encode()))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path(s.id))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._cache.pop(s.id, None)

    def get(self, sid: str, fresh: bool = False) -> Session:
        """The session; re-read only when the file changed (cheap enough per turn decision)."""
        path = self._path(sid)
        try:
            st = path.stat()
        except FileNotFoundError:
            raise KeyError(sid) from None
        stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
        hit = self._cache.get(sid)
        if hit and hit[0] == stamp and not fresh:
            return hit[1]
        s = self._decode(path.read_bytes())
        if s.id != sid:
            raise StoreError("session file does not belong to the requested id")
        self._cache[sid] = (stamp, s)
        return s

    def list(self) -> list[Session]:
        out = []
        for p in sorted(self.root.glob("*" + _SUFFIX)):
            out.append(self._decode(p.read_bytes()))
        return sorted(out, key=lambda s: s.id)

    # ---- enrollment gate -----------------------------------------------------------------

    def _check_enrolled(self, user: str) -> None:
        try:
            meta = self.signatures.metadata(user)
        except KeyError:
            raise SessionError(f"{user!r} has no enrolled voice signature") from None
        if not (meta.get("consent") or {}).get("given"):
            raise SessionError(f"{user!r} has not given consent")

    # ---- sessions ------------------------------------------------------------------------

    def create_session(
        self, owner: str, mode: str = "isolation", unknown: str = "drop", room: str | None = None
    ) -> Session:
        if mode not in MODES:
            raise SessionError(f"mode must be one of {MODES}")
        if unknown not in ("drop", "label"):
            raise SessionError("unknown must be 'drop' or 'label'")
        self._check_enrolled(owner)
        sid = secrets.token_hex(8)
        s = Session(sid, owner, mode, room or f"vr-{sid}", unknown, (owner,))
        with self._locked():
            self._write(s)
        return s

    def invite(self, session: Session | str, user: str, ttl_s: float = INVITE_TTL_S) -> str:
        sid = session if isinstance(session, str) else session.id
        self._check_enrolled(user)
        nonce = secrets.token_hex(12)
        exp = self.now() + ttl_s
        with self._locked():
            s = self.get(sid, fresh=True)
            if user in s.members:
                raise SessionError(f"{user!r} is already a member")
            pending = {n: v for n, v in s.pending.items() if v["exp"] > self.now()}
            pending[nonce] = {"user": user, "exp": exp}
            self._write(_with(s, pending=pending))
        payload = _b64(json.dumps({"s": sid, "u": user, "e": exp, "n": nonce}).encode())
        return f"{_PREFIX}.{payload}.{self._mac(payload)}"

    def _mac(self, payload: str) -> str:
        return _b64(hmac.new(self._mac_key, payload.encode(), hashlib.sha256).digest())

    def accept(self, code: str) -> Session:
        """Consume an invite and add its user to the session; every failure is one InviteError."""
        try:
            prefix, payload, mac = code.split(".")
            if prefix != _PREFIX or not hmac.compare_digest(mac, self._mac(payload)):
                raise ValueError
            d = json.loads(_unb64(payload))
            sid, user, exp, nonce = d["s"], d["u"], float(d["e"]), d["n"]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise InviteError("invalid invite code") from None
        if self.now() >= exp:
            raise InviteError("invite expired")
        with self._locked():
            try:
                s = self.get(sid, fresh=True)
            except KeyError:
                raise InviteError("invalid invite code") from None
            rec = s.pending.get(nonce)
            if rec is None or rec["user"] != user:
                raise InviteError("invite already used or revoked")
            try:
                self._check_enrolled(user)
            except SessionError as e:
                raise InviteError("invitee is no longer enrolled") from e
            pending = {n: v for n, v in s.pending.items() if n != nonce}
            members = s.members if user in s.members else (*s.members, user)
            s = _with(
                s, pending=pending, members=members,
                revoked=tuple(r for r in s.revoked if r != user),
            )  # fmt: skip
            self._write(s)
        return s

    def revoke(self, session: Session | str, user: str) -> Session:
        """Remove a member (never the owner) and cancel their pending invites."""
        sid = session if isinstance(session, str) else session.id
        with self._locked():
            s = self.get(sid, fresh=True)
            if user == s.owner:
                raise SessionError("the owner cannot be revoked")
            members = tuple(m for m in s.members if m != user)
            pending = {n: v for n, v in s.pending.items() if v["user"] != user}
            revoked = s.revoked if user in s.revoked else (*s.revoked, user)
            s = _with(s, members=members, pending=pending, revoked=revoked)
            self._write(s)
        return s

    def join_token(
        self, session: Session | str, user: str, url: str, ttl_s: float = TOKEN_TTL_S, **kw
    ) -> str:
        """LiveKit join token (identity=user, attribute voiceroom_user, grant for the room)."""
        s = self.get(session if isinstance(session, str) else session.id)
        if user not in s.members:
            raise SessionError(f"{user!r} is not a member of this session")
        from voiceroom.adapters.livekit_room import make_token

        return make_token(url, s.room, user, {"voiceroom_user": user}, ttl_s=ttl_s, **kw)


def _with(s: Session, **kw) -> Session:
    return Session(**{**s.__dict__, **kw})


# ---- membership-driven policy -------------------------------------------------------------


class SessionPolicy:
    """Policy for one source whose decisions follow the session's *current* membership.

    Isolation: only the owner is forwarded. Collaboration: a member whose track claims them is
    verified 1:1; a source that is not (or no longer) a member is handled as shared/unknown
    (voices of non-members follow the session's `unknown` policy)."""

    def __init__(
        self, store: SessionStore, session_id: str, claimed: str | None, **policy_kw
    ) -> None:
        self.store, self.sid, self.claimed, self.kw = store, session_id, claimed, policy_kw
        self._matcher = None
        self._built: tuple | None = None
        self._inner = None

    def bind_matcher(self, matcher) -> None:
        self._matcher = matcher
        self._built = None

    def _policy(self):
        s = self.store.get(self.sid)
        sig = (s.mode, s.members, s.unknown, self.claimed in s.members)
        if sig != self._built:
            self._inner = self._build(s)
            if self._matcher is not None:
                self._inner.bind_matcher(self._matcher)
            self._built = sig
        return self._inner

    def _build(self, s: Session):
        kw = self.kw
        if s.mode == "isolation":
            if self.claimed == s.owner:
                return TrackPolicy("verified", {s.owner}, s.owner, "drop", **kw)
            emb = kw.get("embedder", "")
            thr = kw.get("threshold")
            thr = threshold_for(emb) if thr is None else thr
            return IsolationPolicy(s.owner, thr, embedder=emb)
        if self.claimed in s.members:
            return TrackPolicy("verified", s.members, self.claimed, s.unknown, **kw)
        return TrackPolicy("shared", s.members, None, s.unknown, **kw)

    def decide(self, match, final: bool = True):
        return self._policy().decide(match, final)

    def windows_ok(self, matches, label=None, short=False) -> bool:
        return self._policy().windows_ok(matches, label, short)

    def windows_rule(self, matches, label=None, short=False):
        return self._policy().windows_rule(matches, label, short)

    def describe_windows(self, matches, label, short, rule) -> str:
        return self._policy().describe_windows(matches, label, short, rule)

    def __getattr__(self, name):  # thresholds etc. of the current inner policy
        if name.startswith("_") or name in ("store", "sid", "claimed", "kw"):
            raise AttributeError(name)
        return getattr(self._policy(), name)


class SessionSync:
    """Keeps a running RoomPipeline in line with the session: a participant whose claimed
    identity is no longer a member is dropped from the pipeline and kicked from the room.

    `kick(participant_id, identity)` removes the participant from the transport (FakeRoom.kick,
    or the LiveKit RoomService behind `adapters.livekit_room.make_kicker`)."""

    def __init__(
        self,
        store: SessionStore,
        session_id: str,
        pipeline,
        kick: Callable[[str, str], Awaitable],
        agent=None,
    ):
        """`agent` (a RoomAgent) supplies the connected-participant roster, so revoked members
        are kicked even when they publish no audio; without it only pipeline sources are seen."""
        self.store, self.sid, self.pipeline, self.kick = store, session_id, pipeline, kick
        self.agent = agent
        self.kicked: list[str] = []
        self._pending: dict[str, str] = {}  # participant id -> transport identity, kick pending
        pipeline.deliver_gate = self._allowed

    def _allowed(self, claimed: str | None, label: str | None) -> bool:
        """Delivery gate: a queued turn of a since-revoked member is not forwarded."""
        if label == UNKNOWN_LABEL:  # the policy already applied the session's unknown rule
            return True
        s = self.store.get(self.sid)
        if claimed in s.revoked:
            return False
        # a never-member shared device is fine when the voice matched a current member
        return label is None or label in s.members

    def policy_factory(self, **policy_kw) -> Callable[[str, str | None], SessionPolicy]:
        return lambda source_id, claimed: SessionPolicy(self.store, self.sid, claimed, **policy_kw)

    async def sync_once(self) -> None:
        """Drop and kick revoked members (failed kicks retry on the next call), whether or not
        they publish audio. Never-members stay: the policy treats them as unknown."""
        revoked = set(self.store.get(self.sid).revoked)
        roster = {}  # participant id -> (claimed, transport identity)
        for sid, src in self.pipeline.sources.items():
            roster[sid] = (src.claimed, src.claimed)
        if self.agent is not None:
            for pid, info in self.agent.participants.items():
                claimed = info.attributes.get(IDENTITY_ATTRIBUTE) or info.identity
                roster[pid] = (claimed, info.identity or claimed)
        for pid, (claimed, identity) in roster.items():
            if claimed in revoked:
                self.pipeline.remove_source(pid)  # no-op without a source
                self._pending[pid] = identity
        for pid, identity in list(self._pending.items()):
            try:
                await asyncio.wait_for(self.kick(pid, identity), KICK_TIMEOUT_S)
            except Exception:
                log.warning("kick failed for a revoked participant; will retry")
                continue
            del self._pending[pid]
            self.kicked.append(pid)

    async def run(self, interval_s: float = SYNC_INTERVAL_S) -> None:
        while True:
            await self.sync_once()
            await asyncio.sleep(interval_s)


__all__ = [
    "INVITE_TTL_S", "InviteError", "MODES", "Session", "SessionError", "SessionPolicy",
    "SessionStore", "SessionSync", "UNKNOWN_LABEL",
]  # fmt: skip
