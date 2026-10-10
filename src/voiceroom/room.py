"""Multi-source room pipeline: N audio sources -> one provider, one commit at a time."""

import asyncio
import functools
import logging
import time
from collections import deque
from collections.abc import AsyncIterable, Callable, Iterable
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import correlate

from .adapters.base import Provider
from .audio import SAMPLE_RATE
from .matcher import Match, Matcher
from .pipeline import (
    IDENTIFY_TIMEOUT_S,
    MAX_INFLIGHT_IDENTIFY,
    MAX_PENDING_TURNS,
    MAX_RECORDS,
    SEND_TIMEOUT_S,
    START_TIMEOUT_S,
    DecidedTurn,
    PendingTurn,
    SourceProcessor,
    TurnRecord,
    _aiter,
    _Identifier,
    _ProcIdentifier,
    _return,
    _StageError,
    _ThreadIdentifier,
)
from .policy import CollaborationPolicy, IsolationPolicy, TrackPolicy, Verdict
from .segmenter import Segmenter
from .vad import FRAME_SIZE, VAD, SileroVAD

log = logging.getLogger(__name__)

CATCH_UP_MAX_S = 1.0  # longest a commit waits for lagging sources before going ahead

BLEED_MIN_OVERLAP = 0.5  # share of the quieter turn that must overlap the louder one in time
BLEED_MIN_DB = 3.0  # the quieter turn must be at least this much below the louder (speech RMS)
BLEED_MIN_CORR = 0.7  # required waveform correlation with the louder turn
BLEED_MAX_LAG_S = 0.3  # search range around the nominal alignment
BLEED_HISTORY = 16  # recently committed turns kept for the check

_OPEN = object()  # marks a candidate original that is still open in its segmenter

Policy = IsolationPolicy | CollaborationPolicy | TrackPolicy


@dataclass(frozen=True)
class RoomTurnRecord(TurnRecord):
    """A TurnRecord on the room clock, plus its source and time spent waiting to commit."""

    source_id: str = ""
    queue_wait_s: float = 0.0  # decided -> commit started (waiting behind earlier turns)
    claimed_identity: str | None = None
    bleed_of: str | None = None  # dropped as a cross-talk copy of this source's turn


@dataclass(frozen=True)
class _Seen:
    """A committed turn, as the bleed detector needs it."""

    source_id: str
    t_start: float  # room time
    t_end: float
    level_db: float
    ident: str | None  # the identity it was committed under (label sent, else claimed)
    audio: np.ndarray  # kept in memory only, for the correlation check; never logged
    t0: float  # room time of audio[0]


def _corr_peak(q: np.ndarray, tq: float, l: np.ndarray, tl: float) -> float:  # noqa: E741
    """Normalized cross-correlation peak of `q` against `l` within BLEED_MAX_LAG_S of the
    alignment given by the room times (a delayed, attenuated copy scores near 1)."""
    if len(q) < 2 or len(l) < 2:
        return 0.0
    qz = q.astype(np.float64) - np.mean(q)
    lz = l.astype(np.float64) - np.mean(l)
    corr = correlate(lz, qz, mode="full", method="fft")  # index k <-> lag k - (len(q) - 1)
    lags = np.arange(len(corr)) - (len(qz) - 1)  # q[i] pairs with l[i + lag]
    nominal = round((tq - tl) * SAMPLE_RATE)
    keep = np.abs(lags - nominal) <= BLEED_MAX_LAG_S * SAMPLE_RATE
    if not keep.any():
        return 0.0
    cs = np.concatenate([[0.0], np.cumsum(lz**2)])
    lo = np.clip(lags, 0, len(lz))
    hi = np.clip(lags + len(qz), 0, len(lz))
    e_l = cs[hi] - cs[lo]
    denom = np.sqrt(np.sum(qz**2) * e_l) + 1e-9
    return float(np.max((corr / denom)[keep]))


def _correlated(q: np.ndarray, tq: float, l: np.ndarray, tl: float) -> bool:  # noqa: E741
    return _corr_peak(q, tq, l, tl) >= BLEED_MIN_CORR


def _level_db(ev) -> float:
    """RMS (dB) over the turn's voiced frames, else over all of it."""
    a = ev.audio
    n = len(a) // FRAME_SIZE
    if ev.voiced is not None and n and len(ev.voiced) >= n and ev.voiced[:n].any():
        a = a[: n * FRAME_SIZE].reshape(n, FRAME_SIZE)[ev.voiced[:n]]
    p = float(np.mean(np.square(a, dtype=np.float64))) if len(a) else 0.0
    return 10 * np.log10(p + 1e-12)


@dataclass
class _Entry:
    t_end: float  # room time
    seq: int
    source_id: str
    pt: PendingTurn
    offset: float
    claimed: str | None
    task: "asyncio.Task | None" = None
    decided_at: float = 0.0
    deps: list = field(default_factory=list)  # (entry, decided turn, wait, source) held as copies
    sort_key: tuple = field(init=False)

    def __post_init__(self) -> None:
        self.sort_key = (self.t_end, self.seq)


@dataclass
class _Source:
    proc: SourceProcessor
    frames: AsyncIterable[np.ndarray] | Iterable[np.ndarray]
    claimed: str | None
    offset: float  # room time of the source's first frame
    task: "asyncio.Task | None" = None


class RoomPipeline:
    """One SourceProcessor per source, one shared matcher and provider.

    Accepted turns go through a commit queue ordered by end of speech (room time): at most one
    `send_turn` is in flight, and a turn is never committed ahead of an earlier-ending turn that
    is still being decided. Sources may be added and removed while `run()` is active. Room time
    is the source's stream time plus its offset; a source added without `t_offset` starts at the
    latest stream time seen so far.
    """

    def __init__(
        self,
        provider: Provider,
        policy_factory: Callable[[str, str | None], Policy],
        matcher: Matcher,
        vad_factory: Callable[[], VAD] = SileroVAD,
        segmenter_factory: Callable[[], Segmenter] = Segmenter,
        max_pending: int = MAX_PENDING_TURNS,
        send_timeout_s: float = SEND_TIMEOUT_S,
        identify_timeout_s: float = IDENTIFY_TIMEOUT_S,
        max_inflight_identify: int = MAX_INFLIGHT_IDENTIFY,
        matcher_factory: Callable[[], Matcher] | None = None,
        isolation: str = "process",
        start_timeout_s: float = START_TIMEOUT_S,
        on_record: Callable[[RoomTurnRecord], None] | None = None,
        max_records: int = MAX_RECORDS,
        bleed: bool = True,
        deliver_gate: Callable[[str, str | None], bool] | None = None,
    ):
        if isolation not in ("process", "thread"):
            raise ValueError("isolation must be 'process' or 'thread'")
        self.provider, self.policy_factory, self.matcher = provider, policy_factory, matcher
        self.vad_factory, self.segmenter_factory = vad_factory, segmenter_factory
        self.max_pending, self.send_timeout_s = max_pending, send_timeout_s
        self.identify_timeout_s, self.max_inflight = identify_timeout_s, max_inflight_identify
        self.matcher_factory, self.isolation = matcher_factory, isolation
        self.start_timeout_s = start_timeout_s
        self.on_record, self.records = on_record, deque(maxlen=max_records)
        self.bleed = bleed
        # (claimed identity, label) -> False drops an accepted turn at delivery time (e.g. the
        # speaker was revoked while the turn waited in the commit queue)
        self.deliver_gate = deliver_gate
        self._open_deps: dict[str, list] = {}  # source -> copies held until its open turn ends
        self._seen: deque[_Seen] = deque(maxlen=BLEED_HISTORY)
        self.sources: dict[str, _Source] = {}
        self._entries: list[_Entry] = []
        self._seq = 0
        self._inflight = 0
        self._running = False
        self._closing = False
        self._wake = asyncio.Event()
        self._ident: _Identifier | None = None
        self.max_concurrent_sends = 0  # peak overlapping send_turn calls (must stay 1)
        self._sending = 0

    # ---- sources -------------------------------------------------------------------------

    def add_source(
        self,
        source_id: str,
        frames: AsyncIterable[np.ndarray] | Iterable[np.ndarray],
        claimed_identity: str | None = None,
        t_offset: float | None = None,
    ) -> None:
        """Register a source of 512-sample frames; before or during `run()`."""
        if source_id in self.sources:
            raise ValueError(f"source {source_id!r} already added")
        proc = SourceProcessor(
            source_id,
            self.vad_factory(),
            self.segmenter_factory(),
            self.matcher,
            self.policy_factory(source_id, claimed_identity),
        )
        offset = self._room_now() if t_offset is None else t_offset
        src = _Source(proc, frames, claimed_identity, offset)
        proc.bind(self._identify, self._has_room)
        proc.reset()
        self.sources[source_id] = src
        if self._running:
            self._start(source_id, src)

    def remove_source(self, source_id: str) -> None:
        """Stop a source and flush its open turn into the commit queue."""
        src = self.sources.pop(source_id, None)
        if src is None:  # already ended on its own (its open turn was flushed then)
            return
        if src.task is not None:
            src.task.cancel()
        self._finish_source(source_id, src)

    def _room_now(self) -> float:
        return max((s.offset + s.proc._stream_t for s in self.sources.values()), default=0.0)

    def _start(self, source_id: str, src: _Source) -> None:
        src.task = asyncio.create_task(self._feed(source_id, src))

    async def _feed(self, source_id: str, src: _Source) -> None:
        try:
            async for frame in _aiter(src.frames):
                self._enqueue(source_id, src, src.proc.push(frame))
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise  # remove_source flushes
        except Exception:
            log.warning("source %s failed", source_id)
        if self.sources.get(source_id) is src:
            self.sources.pop(source_id)
            self._finish_source(source_id, src)

    def _finish_source(self, source_id: str, src: _Source) -> None:
        self._enqueue(source_id, src, src.proc.flush())
        src.proc.close()
        self._wake.set()

    # ---- commit queue --------------------------------------------------------------------

    def _has_room(self) -> bool:
        return len(self._entries) < self.max_pending and self._inflight < self.max_inflight - 2

    def _enqueue(self, source_id: str, src: _Source, turns: list[PendingTurn]) -> None:
        for pt in turns:
            if len(self._entries) >= self.max_pending:
                pt.cancel()
                self._error(source_id, src, pt, "overflow")
                continue
            self._seq += 1
            e = _Entry(src.offset + pt.ev.t_end, self._seq, source_id, pt, src.offset, src.claimed)
            e.task = asyncio.create_task(self._decide(src.proc, e))
            e.deps.extend(self._open_deps.pop(source_id, []))
            self._entries.append(e)
        if turns:
            self._wake.set()

    async def _decide(self, proc: SourceProcessor, e: _Entry):
        try:
            return await proc.decide(e.pt)
        finally:
            e.decided_at = time.perf_counter()
            self._wake.set()

    async def _committer(self) -> None:
        while True:
            if not self._entries:
                if self._closing and not self.sources:
                    await self._flush_open_deps()
                    return
                self._wake.clear()
                await self._wake.wait()
                continue
            e = min(self._entries, key=lambda x: x.sort_key)
            assert e.task is not None
            await asyncio.wait([e.task])
            if min(self._entries, key=lambda x: x.sort_key) is not e:
                continue  # an earlier-ending turn arrived meanwhile
            await self._others_caught_up(e)
            if min(self._entries, key=lambda x: x.sort_key) is not e:
                continue
            self._entries.remove(e)
            await self._commit(e)

    async def _flush_open_deps(self) -> None:
        """Copies held against an original whose turn never reached the queue: send them."""
        held, self._open_deps = self._open_deps, {}
        for deps in held.values():
            for de, ddt, dwait, _ in deps:
                try:
                    await self._deliver(de, ddt, dwait)
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception as exc:
                    self._emit_error(de, exc)

    def _behind(self, e: _Entry) -> bool:
        """Another live source has not yet streamed past `e` plus its endpoint hangover, so it
        could still emit a turn that ended earlier."""
        for sid, s in self.sources.items():
            if sid != e.source_id:
                lag = s.proc.segmenter.end_silence * FRAME_SIZE / SAMPLE_RATE
                if s.offset + s.proc._stream_t < e.t_end + lag:
                    return True
        return False

    async def _others_caught_up(self, e: _Entry) -> None:
        # Bounded: a source that stops sending frames (muted) must not stall the room.
        deadline = time.perf_counter() + CATCH_UP_MAX_S
        while self._behind(e) and time.perf_counter() < deadline:
            await asyncio.sleep(0.005)

    async def _commit(self, e: _Entry) -> None:
        assert e.task is not None
        covered = False  # its speech reached the provider (or is a dropped copy of such speech)
        try:
            dt = e.task.result()
            wait = time.perf_counter() - e.decided_at
            hit = None
            if self.bleed and dt.decision.verdict is Verdict.ACCEPT:
                hit = await self._bleed_of(e, dt)
            if hit is not None:
                sid, orig = hit
                if orig is _OPEN:  # original still being spoken: hold until its turn is decided
                    self._open_deps.setdefault(sid, []).extend([*e.deps, (e, dt, wait, sid)])
                    e.deps = []
                    return
                if orig is not None:  # original not delivered yet: decide when it is
                    orig.deps.extend(e.deps)
                    e.deps = []
                    orig.deps.append((e, dt, wait, sid))
                    return
                covered = True
                self._emit(self._record(e, dt, wait, Verdict.REJECT, sid))
            else:
                covered = await self._deliver(e, dt, wait)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as exc:
            self._emit_error(e, exc)
        finally:
            e.pt.cancel()
            await self._resolve(e, covered)

    async def _deliver(self, e: _Entry, dt: DecidedTurn, wait: float) -> bool:
        """Send an accepted turn (one at a time) and record it; True if it was sent."""
        verdict = dt.decision.verdict
        if (
            verdict is Verdict.ACCEPT
            and self.deliver_gate
            and not self.deliver_gate(e.claimed, dt.label)
        ):
            verdict = Verdict.REJECT
        sent = verdict is Verdict.ACCEPT
        if sent:
            self._sending += 1
            self.max_concurrent_sends = max(self.max_concurrent_sends, self._sending)
            try:
                await asyncio.wait_for(
                    self.provider.send_turn(dt.audio, dt.label), self.send_timeout_s
                )
            except TimeoutError as exc:
                raise _StageError("send_timeout") from exc
            except Exception as exc:
                raise _StageError("send_error") from exc
            finally:
                self._sending -= 1
            if self.bleed:
                self._remember(e, dt)
        self._emit(self._record(e, dt, wait, verdict, None))
        return sent

    async def _resolve(self, e: _Entry, covered: bool) -> None:
        """Copies held against `e`: dropped if its speech was delivered, else sent themselves."""
        deps, e.deps = e.deps, []
        for de, ddt, dwait, sid in deps:
            try:
                if covered:
                    self._emit(self._record(de, ddt, dwait, Verdict.REJECT, sid))
                else:
                    await self._deliver(de, ddt, dwait)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as exc:
                self._emit_error(de, exc)

    def _record(self, e: _Entry, dt: DecidedTurn, wait: float, verdict, bleed_of) -> RoomTurnRecord:
        return RoomTurnRecord(
            e.offset + dt.t_start, e.t_end, verdict, dt.match.name, dt.match.score, dt.early,
            dt.endpoint_s, time.perf_counter() - e.pt.t0, None, e.source_id, wait, e.claimed,
            bleed_of,
        )  # fmt: skip

    def _emit_error(self, e: _Entry, exc: Exception) -> None:
        code = getattr(exc, "code", "turn_error")  # fixed code only: no audio in payloads
        log.warning("turn dropped: %s", code)
        self._emit(self._error_record(e.source_id, e.offset, e.claimed, e.pt, code))

    # ---- cross-talk bleed ----------------------------------------------------------------

    def _span(self, e: _Entry) -> tuple[float, float]:
        return e.offset + e.pt.ev.t_start, e.t_end

    @staticmethod
    def _t0(e: _Entry) -> float:
        """Room time of the first sample of the turn's audio (it starts with a pre-roll)."""
        ev = e.pt.ev
        speech = len(ev.audio) - ev.tail_samples - round((ev.t_end - ev.t_start) * SAMPLE_RATE)
        return e.offset + ev.t_start - speech / SAMPLE_RATE

    def _remember(self, e: _Entry, dt: DecidedTurn) -> None:
        a, b = self._span(e)
        ident = dt.label or e.claimed
        self._seen.append(
            _Seen(e.source_id, a, b, _level_db(e.pt.ev), ident, dt.audio, self._t0(e))
        )

    def _open_candidates(self, e: _Entry) -> list:
        """Turns still open in other sources (original not decided yet), from their buffers."""
        out = []
        for sid, s in self.sources.items():
            since = s.proc.segmenter.open_since
            if sid == e.source_id or since is None:
                continue
            seg = s.proc.segmenter
            audio, voiced = seg.turn_audio(), seg.turn_voiced()
            n = min(len(voiced), len(audio) // FRAME_SIZE)
            if not n:
                continue
            fr = audio[: n * FRAME_SIZE].reshape(n, FRAME_SIZE)
            x = fr[voiced[:n]] if voiced[:n].any() else fr
            level = 10 * np.log10(float(np.mean(np.square(x, dtype=np.float64))) + 1e-12)
            now = s.offset + s.proc._stream_t
            t0 = now - len(audio) / SAMPLE_RATE
            out.append((sid, s.offset + since, now, level, s.claimed, audio, t0, _OPEN))
        return out

    async def _bleed_of(self, e: _Entry, dt: DecidedTurn) -> tuple[str, _Entry | None] | None:
        """(source, pending original or None) whose speech this accepted turn is a quieter copy
        of, else None.

        A copy: >= BLEED_MIN_OVERLAP of this turn overlaps a louder (>= BLEED_MIN_DB) turn from
        another source, this turn does not verify as its own track's owner, and either its voice
        is that other track's identity or it matches no member but correlates with that audio.
        Only the quieter side is ever dropped. An original not yet delivered is returned so the
        caller can hold the copy until it is (a failed send must not swallow both).
        """
        if e.claimed is not None and dt.label == e.claimed:
            return None  # verified as this track's owner: never dropped
        a, b = self._span(e)
        level = _level_db(e.pt.ev)
        pend = [
            o for o in self._entries
            if o is not e and o.source_id != e.source_id and self._overlap(a, b, *self._span(o))
        ]  # fmt: skip
        if pend:
            await asyncio.wait([o.task for o in pend if o.task is not None])
        cands = [
            (s.source_id, s.t_start, s.t_end, s.level_db, s.ident, s.audio, s.t0, None)
            for s in self._seen
        ]
        for o in pend:
            if o.task is None or o.task.cancelled() or o.task.exception() is not None:
                continue
            od = o.task.result()
            if od.decision.verdict is Verdict.ACCEPT:
                oa, ob = self._span(o)
                cands.append((
                    o.source_id, oa, ob, _level_db(o.pt.ev), od.label or o.claimed,
                    od.audio, self._t0(o), o,
                ))  # fmt: skip
        cands += self._open_candidates(e)
        for sid, oa, ob, olevel, ident, oaudio, ot0, orig in cands:
            if sid == e.source_id or olevel - level < BLEED_MIN_DB:
                continue
            if not self._overlap(a, b, oa, ob):
                continue
            # voice compatible with the other track, and the same speech (not just the same voice)
            voice = dt.match.name is None or (
                ident is not None and ident in (dt.match.name, dt.label)
            )
            if voice and _correlated(dt.audio, self._t0(e), oaudio, ot0):
                return sid, orig
        return None

    @staticmethod
    def _overlap(a: float, b: float, oa: float, ob: float) -> bool:
        return min(b, ob) - max(a, oa) >= BLEED_MIN_OVERLAP * max(b - a, 1e-6)

    def _error_record(self, sid, offset, claimed, pt: PendingTurn, code: str) -> RoomTurnRecord:
        ev = pt.ev
        return RoomTurnRecord(
            offset + ev.t_start, offset + ev.t_end, Verdict.REJECT, None, 0.0, False,
            max(0.0, pt.now - ev.t_end), time.perf_counter() - pt.t0, code, sid, 0.0, claimed,
        )  # fmt: skip

    def _error(self, sid: str, src: _Source, pt: PendingTurn, code: str) -> None:
        self._emit(self._error_record(sid, src.offset, src.claimed, pt, code))

    def _emit(self, rec: RoomTurnRecord) -> None:
        self.records.append(rec)
        if self.on_record is not None:
            try:
                self.on_record(rec)
            except Exception:
                log.warning("on_record callback failed")

    # ---- identification ------------------------------------------------------------------

    def _make_identifier(self) -> _Identifier:
        if self.isolation == "thread":
            return _ThreadIdentifier(self.matcher, self.max_inflight)
        factory = self.matcher_factory or functools.partial(_return, self.matcher)
        return _ProcIdentifier(factory, self.start_timeout_s)

    async def _identify(self, audio: np.ndarray) -> Match:
        if self._inflight >= self.max_inflight:
            raise _StageError("identify_busy")
        assert self._ident is not None
        self._inflight += 1
        try:
            return await self._ident.identify(audio, self.identify_timeout_s)
        except TimeoutError as exc:
            raise _StageError("identify_timeout") from exc
        except Exception as exc:
            raise _StageError("identify_error") from exc
        finally:
            self._inflight -= 1

    # ---- run -----------------------------------------------------------------------------

    def finish(self) -> None:
        """No more sources will be added: `run()` returns once the current ones end."""
        self._closing = True
        self._wake.set()

    async def run(self, stop_when_idle: bool = False) -> None:
        """Run until `finish()` is called (or, with `stop_when_idle`, until no source is left)
        and every queued turn is committed or dropped."""
        self._ident = self._make_identifier()
        self.records.clear()
        self._inflight = 0
        self._running = True
        await self.provider.connect()
        committer = asyncio.create_task(self._committer())
        try:
            for sid, src in list(self.sources.items()):
                self._start(sid, src)
            while self.sources or not (self._closing or stop_when_idle):
                self._wake.clear()
                tasks = [s.task for s in self.sources.values() if s.task is not None]
                waiter = asyncio.create_task(self._wake.wait())
                await asyncio.wait([waiter, *tasks], return_when=asyncio.FIRST_COMPLETED)
                waiter.cancel()
            self._closing = True
            self._wake.set()
            await committer
        finally:
            self._running = False
            committer.cancel()
            for src in self.sources.values():
                if src.task is not None:
                    src.task.cancel()
                src.proc.close()
            for e in self._entries:
                e.pt.cancel()
                if e.task is not None:
                    e.task.cancel()
            self._entries.clear()
            self._ident.close()
            await self.provider.close()
