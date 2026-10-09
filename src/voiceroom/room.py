"""Multi-source room pipeline: N audio sources -> one provider, one commit at a time."""

import asyncio
import functools
import logging
import time
from collections import deque
from collections.abc import AsyncIterable, Callable, Iterable
from dataclasses import dataclass, field

import numpy as np

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
from .policy import CollaborationPolicy, IsolationPolicy, Verdict
from .segmenter import Segmenter
from .vad import FRAME_SIZE, VAD, SileroVAD

log = logging.getLogger(__name__)

CATCH_UP_MAX_S = 1.0  # longest a commit waits for lagging sources before going ahead

Policy = IsolationPolicy | CollaborationPolicy


@dataclass(frozen=True)
class RoomTurnRecord(TurnRecord):
    """A TurnRecord on the room clock, plus its source and time spent waiting to commit."""

    source_id: str = ""
    queue_wait_s: float = 0.0  # decided -> commit started (waiting behind earlier turns)
    claimed_identity: str | None = None


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
        pt = e.pt
        try:
            dt = e.task.result()
            wait = time.perf_counter() - e.decided_at
            if dt.decision.verdict is Verdict.ACCEPT:
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
            self._emit(
                RoomTurnRecord(
                    e.offset + dt.t_start,
                    e.t_end,
                    dt.decision.verdict,
                    dt.match.name,
                    dt.match.score,
                    dt.early,
                    dt.endpoint_s,
                    time.perf_counter() - pt.t0,
                    None,
                    e.source_id,
                    wait,
                    e.claimed,
                )  # fmt: skip
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as exc:
            code = getattr(exc, "code", "turn_error")  # fixed code only: no audio in payloads
            log.warning("turn dropped: %s", code)
            self._emit(self._error_record(e.source_id, e.offset, e.claimed, pt, code))
        finally:
            pt.cancel()

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
