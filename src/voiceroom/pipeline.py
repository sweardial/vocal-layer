"""Streaming pipeline: frames -> VAD -> segmenter -> identify -> policy -> provider."""

import asyncio
import functools
import logging
import multiprocessing as mp
import pickle
import threading
import time
from collections import deque
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .adapters.base import Provider
from .audio import SAMPLE_RATE
from .config import CONTINUITY_MIN_S
from .matcher import Match, Matcher
from .policy import CollaborationPolicy, Decision, IsolationPolicy, Verdict
from .segmenter import IdentifyReady, Segmenter, SpeechStart, TurnEnd
from .vad import FRAME_SIZE, VAD

log = logging.getLogger(__name__)

MAX_RECORDS = 1000  # retained TurnRecords (oldest dropped); use on_record to keep them all
MAX_PENDING_TURNS = 4  # turns waiting for identify/commit; further turns are dropped
SEND_TIMEOUT_S = 5.0  # a provider send slower than this drops the turn
IDENTIFY_TIMEOUT_S = 5.0  # an identification slower than this drops the turn
MAX_INFLIGHT_IDENTIFY = 6  # submitted-but-unfinished identifications (running or queued)
START_TIMEOUT_S = 60.0  # identification child start-up (imports, model load)
WIN_FRAMES = 16  # continuity windows: 16 frames (~0.5 s) every 8 frames (~0.25 s)
HOP_FRAMES = 8
MIN_VOICED = 0.5  # windows with less speech than this are not scored (pauses)


class BargeInSink(Protocol):
    @property
    def active(self) -> bool:
        """A reply is queued or playing."""
        ...

    def interrupt(self) -> int:
        """Flush queued audio; returns ms of the current reply already played."""
        ...


@dataclass(frozen=True)
class TurnRecord:
    t_start: float  # stream time (s)
    t_end: float
    verdict: Verdict  # ACCEPT (committed) or REJECT (dropped)
    speaker: str | None  # matcher's best name for the deciding match (None = unknown)
    score: float
    early: bool  # decided on partial audio, before the turn ended
    endpoint_s: float  # end of speech -> TurnEnd emitted (stream time; segmenter hangover)
    compute_s: float  # TurnEnd emitted -> commit/drop (wall time)
    error: str | None = None  # fixed code if the turn was dropped by a failure, else None

    @property
    def latency_s(self) -> float:
        """End of speech -> commit (or drop)."""
        return self.endpoint_s + self.compute_s


@dataclass
class PendingTurn:
    """An ended turn awaiting its decision, with the identification tasks started for it."""

    ev: TurnEnd
    early: "asyncio.Task[Match] | None"
    early_len: int
    wins: "dict[int, asyncio.Task[Match] | None]"
    now: float  # stream time at TurnEnd
    t0: float  # perf_counter at TurnEnd

    def cancel(self) -> None:
        if self.early is not None:
            self.early.cancel()
        for t in self.wins.values():
            if t is not None:
                t.cancel()


@dataclass(frozen=True)
class DecidedTurn:
    source_id: str
    audio: np.ndarray
    label: str | None
    decision: Decision
    match: Match
    early: bool  # decided on partial audio, before the turn ended
    t_start: float
    t_end: float
    endpoint_s: float  # end of speech -> TurnEnd emitted (stream time)


class SourceProcessor:
    """One audio source: frames -> VAD -> segmenter -> identify -> policy -> decided turns.

    Never touches the provider. The owner supplies `identify` (async, bounded, may raise) and
    `has_room` via `bind`, feeds frames to `push`/`flush`, and calls `decide` on each pending turn.
    """

    def __init__(
        self,
        source_id: str,
        vad: VAD,
        segmenter: Segmenter,
        matcher: Matcher,
        policy: IsolationPolicy | CollaborationPolicy,
    ):
        self.source_id, self.vad, self.segmenter = source_id, vad, segmenter
        self.matcher, self.policy = matcher, policy
        policy.bind_matcher(matcher)
        self._identify_fn: Callable[[np.ndarray], Awaitable[Match]] = _unbound
        self._has_room: Callable[[], bool] = lambda: True
        self._on_early_accept: Callable[[], None] | None = None
        self._clear()

    def bind(
        self,
        identify: Callable[[np.ndarray], Awaitable[Match]],
        has_room: Callable[[], bool],
        on_early_accept: Callable[[], None] | None = None,
    ) -> None:
        self._identify_fn, self._has_room = identify, has_room
        self._on_early_accept = on_early_accept

    def _clear(self) -> None:
        self._early: asyncio.Task[Match] | None = None
        self._wins: dict[int, asyncio.Task[Match] | None] = {}  # window start -> score
        self._early_len = 0
        self._stream_t = 0.0  # stream time of the next frame's start

    def reset(self) -> None:
        self.vad.reset()
        self.segmenter.reset()
        self.close()

    def close(self) -> None:
        """Cancel identification tasks of the open turn."""
        if self._early is not None:
            self._early.cancel()
        for t in self._wins.values():
            if t is not None:
                t.cancel()
        self._clear()

    def _spawn(self, audio: np.ndarray) -> "asyncio.Task[Match]":
        t = asyncio.create_task(self._identify_fn(audio))
        t.add_done_callback(lambda t: t.cancelled() or t.exception())  # mark retrieved
        return t

    def _on_early(self, task: "asyncio.Task[Match]") -> None:
        if task.cancelled() or task.exception() is not None or self._on_early_accept is None:
            return
        if self.policy.decide(task.result(), final=False).verdict is Verdict.ACCEPT:
            self._on_early_accept()

    def _handle(self, events: list, now: float) -> list[PendingTurn]:
        out = []
        for ev in events:
            if isinstance(ev, SpeechStart):
                self.close_open()
            elif isinstance(ev, IdentifyReady):
                self._early_len = len(ev.audio)
                if self._has_room():  # else the turn is decided at TurnEnd
                    self._early = self._spawn(ev.audio)
                    self._early.add_done_callback(self._on_early)
            elif isinstance(ev, TurnEnd):
                out.append(
                    PendingTurn(
                        ev, self._early, self._early_len, dict(self._wins), now, time.perf_counter()
                    )
                )
                self._early = None
                self._wins.clear()
        return out

    def close_open(self) -> None:
        t = self._stream_t
        self.close()
        self._stream_t = t

    def push(self, frame: np.ndarray) -> list[PendingTurn]:
        """Process one 512-sample frame; returns turns that ended on it."""
        prob = self.vad.process(frame)
        self._stream_t += len(frame) / 16000
        out = self._handle(self.segmenter.process(frame, prob), self._stream_t)
        # Score each ~0.5 s window of the open turn as soon as it is complete, so the
        # speaker-continuity check costs little at TurnEnd.
        n, wins = self.segmenter.active_frames, self._wins
        nxt = (max(wins) + HOP_FRAMES) if wins else 0
        if n >= nxt + WIN_FRAMES and self._has_room():
            audio, voiced = self.segmenter.turn_audio(), self.segmenter.turn_voiced()
            while n >= nxt + WIN_FRAMES:
                if voiced[nxt : nxt + WIN_FRAMES].mean() >= MIN_VOICED:
                    wins[nxt] = self._spawn(_window(audio, voiced, nxt, WIN_FRAMES))
                else:
                    wins[nxt] = None
                nxt += HOP_FRAMES
        return out

    def flush(self) -> list[PendingTurn]:
        return self._handle(self.segmenter.flush(), self._stream_t)

    async def decide(self, pt: PendingTurn) -> DecidedTurn:
        ev = pt.ev
        endpoint_s = max(0.0, pt.now - ev.t_end)
        decision, match, was_early = None, None, False
        if pt.early is not None:
            try:
                match = await pt.early
            except Exception:  # early check is an optimisation: fall back to the full turn
                match = None
        if match is not None:
            decision = self.policy.decide(match, final=False)
            was_early = decision.verdict is not Verdict.UNDECIDED
        if decision is None or decision.verdict is Verdict.UNDECIDED:
            match = await self._identify_fn(ev.audio)
            decision = self.policy.decide(match, final=True)
        assert match is not None and decision is not None
        if decision.verdict is Verdict.ACCEPT and not await self._continuity_ok(
            ev, pt.wins, decision.label
        ):
            decision, was_early = Decision(Verdict.REJECT), False  # speaker change inside the turn
        return DecidedTurn(
            self.source_id, ev.audio, decision.label, decision, match, was_early,
            ev.t_start, ev.t_end, endpoint_s,
        )  # fmt: skip

    async def _continuity_ok(
        self,
        ev: TurnEnd,
        wins: "dict[int, asyncio.Task[Match] | None]",
        label: str | None = None,
    ) -> bool:
        """Score every voiced ~0.5 s window of the turn's speech (plus one anchored at the end)
        and require them to be homogeneous owner speech. Reuses windows scored while the turn
        was running; computes the rest here."""
        n = (len(ev.audio) - ev.tail_samples) // FRAME_SIZE  # frames up to the end of speech
        short = is_short_turn(n)
        voiced = ev.voiced if ev.voiced is not None else np.ones(len(ev.audio) // FRAME_SIZE, bool)
        starts = list(range(0, n - WIN_FRAMES + 1, HOP_FRAMES))
        if n >= WIN_FRAMES and n - WIN_FRAMES not in starts:
            starts.append(n - WIN_FRAMES)
        if n < WIN_FRAMES:
            starts, width = [0], n
        else:
            width = WIN_FRAMES
        matches = []
        for st in starts:
            if width <= 0 or voiced[st : st + width].mean() < MIN_VOICED:
                matches.append(None)  # pause: breaks a run of bad windows
                continue
            t = wins.get(st)
            m = None
            if t is not None:
                try:
                    m = await t
                except Exception:  # best-effort early scoring: recompute below
                    m = None
            if m is None:
                m = await self._identify_fn(_window(ev.audio, voiced, st, width))
            matches.append(m)
        rule = self.policy.windows_rule(matches, label, short=short)
        if rule is not None:
            log.info("%s", self.policy.describe_windows(matches, label, short, rule))
        return rule is None


class Pipeline:
    def __init__(
        self,
        vad: VAD,
        segmenter: Segmenter,
        matcher: Matcher,
        policy: IsolationPolicy | CollaborationPolicy,
        provider: Provider,
        max_pending: int = MAX_PENDING_TURNS,
        send_timeout_s: float = SEND_TIMEOUT_S,
        identify_timeout_s: float = IDENTIFY_TIMEOUT_S,
        max_inflight_identify: int = MAX_INFLIGHT_IDENTIFY,
        matcher_factory: Callable[[], Matcher] | None = None,
        isolation: str = "process",
        start_timeout_s: float = START_TIMEOUT_S,
        on_record: Callable[[TurnRecord], None] | None = None,
        max_records: int = MAX_RECORDS,
        sink: "BargeInSink | None" = None,
    ):
        """Identification runs in a child process that is killed on timeout (`isolation=
        "process"`, the default), so a hung backend can neither retain audio nor block exit.
        The child builds its matcher from `matcher_factory` (picklable, module-level) or, if
        none is given, from a pickled copy of `matcher` (which must then be picklable; ONNX
        sessions usually are not, so live setups pass a factory). `isolation="thread"` runs it
        on a thread instead: a hung backend cannot be killed there (offline smoke use)."""
        if isolation not in ("process", "thread"):
            raise ValueError("isolation must be 'process' or 'thread'")
        self.isolation, self.matcher_factory = isolation, matcher_factory
        if isolation == "process" and matcher_factory is None:
            try:
                pickle.dumps(matcher)
            except Exception as exc:
                raise ValueError(
                    "matcher is not picklable: pass matcher_factory= or isolation='thread'"
                ) from exc
        self.max_pending, self.send_timeout_s = max_pending, send_timeout_s
        self.identify_timeout_s, self.max_inflight = identify_timeout_s, max_inflight_identify
        self.start_timeout_s = start_timeout_s
        self._inflight = 0
        self._lock = threading.Lock()
        self.peak_inflight = 0
        self.matcher, self.provider = matcher, provider
        self.source = SourceProcessor("default", vad, segmenter, matcher, policy)
        self.on_record = on_record
        self.sink = sink  # barge-in: an accepted turn during playback flushes it + interrupts
        self.records: deque[TurnRecord] = deque(maxlen=max_records)  # most recent run/turns

    # vad / segmenter / policy live on the source processor; these stay assignable.
    vad = property(lambda self: self.source.vad, lambda self, v: setattr(self.source, "vad", v))
    segmenter = property(
        lambda self: self.source.segmenter, lambda self, v: setattr(self.source, "segmenter", v)
    )
    policy = property(
        lambda self: self.source.policy, lambda self, v: setattr(self.source, "policy", v)
    )

    def _make_identifier(self) -> "_Identifier":
        if self.isolation == "thread":
            return _ThreadIdentifier(self.matcher, self.max_inflight)
        factory = self.matcher_factory or functools.partial(_return, self.matcher)
        return _ProcIdentifier(factory, self.start_timeout_s)

    async def run(self, frame_source: AsyncIterable[np.ndarray] | Iterable[np.ndarray]) -> None:
        """Consume 512-sample frames until the source ends; commits accepted turns.

        Identification and commits run in a worker task, so the frame loop is never blocked by
        them; a failure on one turn drops that turn and the session continues.
        """
        self._ident = self._make_identifier()
        self.records.clear()
        self._inflight = 0
        await self.provider.connect()
        barge_tasks: set[asyncio.Task] = set()
        queue: asyncio.Queue[PendingTurn | None] = asyncio.Queue(self.max_pending)
        worker = asyncio.create_task(self._worker(queue))

        def has_room() -> bool:
            return queue.qsize() < self.max_pending and self._inflight < self.max_inflight - 2

        def on_early_accept() -> None:
            # Speaker confirmed mid-turn: stop the reply now, not at the end of the turn.
            if self.sink is not None:
                t = asyncio.create_task(self._barge_in())
                barge_tasks.add(t)
                t.add_done_callback(barge_tasks.discard)

        src = self.source
        src.bind(self._identify, has_room, on_early_accept)
        src.reset()

        def enqueue(turns: list[PendingTurn]) -> None:
            for pt in turns:
                try:
                    queue.put_nowait(pt)
                except asyncio.QueueFull:  # never block frame ingestion
                    pt.cancel()
                    self._record_error(pt.ev, "overflow", pt.now, time.perf_counter())

        try:
            async for frame in _aiter(frame_source):
                enqueue(src.push(frame))
                await asyncio.sleep(0)  # let the worker run
            enqueue(src.flush())
            await queue.put(None)  # bounded: waits only for in-flight turns (sends time out)
            await worker
        finally:
            worker.cancel()
            for t in barge_tasks:
                t.cancel()
            src.close()
            self._ident.close()
            await self.provider.close()

    async def _worker(self, queue: "asyncio.Queue[PendingTurn | None]") -> None:
        while (pt := await queue.get()) is not None:
            try:
                await self._finish(pt)
            except Exception as exc:
                # Fixed code only: exception payloads may carry audio or embeddings.
                code = getattr(exc, "code", "turn_error")
                log.warning("turn dropped: %s", code)
                self._record_error(pt.ev, code, pt.now, pt.t0)
            finally:
                for t in pt.wins.values():
                    if t is not None:
                        t.cancel()

    def _emit(self, rec: "TurnRecord") -> None:
        self.records.append(rec)
        if self.on_record is not None:
            try:
                self.on_record(rec)
            except Exception:
                log.warning("on_record callback failed")

    def _record_error(self, ev: TurnEnd, code: str, now: float, t0: float) -> None:
        self._emit(
            TurnRecord(
                ev.t_start,
                ev.t_end,
                Verdict.REJECT,
                None,
                0.0,
                False,
                max(0.0, now - ev.t_end),
                time.perf_counter() - t0,
                code,
            )
        )

    async def _identify(self, audio: np.ndarray) -> Match:
        """Run the matcher on the identification worker, bounded in count and time."""
        with self._lock:
            if self._inflight >= self.max_inflight:
                raise _StageError("identify_busy")
            self._inflight += 1
            self.peak_inflight = max(self.peak_inflight, self._inflight)

        try:
            return await self._ident.identify(audio, self.identify_timeout_s)
        except TimeoutError as exc:
            raise _StageError("identify_timeout") from exc
        except Exception as exc:
            raise _StageError("identify_error") from exc
        finally:
            with self._lock:
                self._inflight -= 1

    async def _barge_in(self) -> None:
        """An accepted turn confirmed while a reply is playing: flush the sink, stop the reply.

        Best effort: a failure here must not drop the user's turn.
        """
        if self.sink is None or not self.sink.active:
            return
        try:
            played_ms = self.sink.interrupt()
            await asyncio.wait_for(self.provider.interrupt(played_ms), self.send_timeout_s)
        except Exception:
            log.warning("barge-in failed", exc_info=False)

    async def _finish(self, pt: PendingTurn) -> None:
        """Decide the turn, then commit it if accepted (`pt.t0` includes queueing delay)."""
        dt = await self.source.decide(pt)
        if dt.decision.verdict is Verdict.ACCEPT:
            await self._barge_in()
            try:
                await asyncio.wait_for(
                    self.provider.send_turn(dt.audio, dt.label), self.send_timeout_s
                )
            except TimeoutError as exc:
                raise _StageError("send_timeout") from exc
            except Exception as exc:
                raise _StageError("send_error") from exc
        self._emit(
            TurnRecord(
                dt.t_start,
                dt.t_end,
                dt.decision.verdict,
                dt.match.name,
                dt.match.score,
                dt.early,
                dt.endpoint_s,
                time.perf_counter() - pt.t0,
            )
        )


def is_short_turn(n_frames: int) -> bool:
    """Turns under CONTINUITY_MIN_S get the lenient `short` continuity rule."""
    return n_frames * FRAME_SIZE < CONTINUITY_MIN_S * SAMPLE_RATE


class _StageError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def _aiter(src):
    if hasattr(src, "__aiter__"):
        async for x in src:
            yield x
    else:
        for x in src:
            yield x


def _window(audio: np.ndarray, voiced: np.ndarray, start: int, width: int) -> np.ndarray:
    """Speech frames only of frames [start, start+width): pauses would skew the embedding."""
    frames = audio[start * FRAME_SIZE : (start + width) * FRAME_SIZE].reshape(-1, FRAME_SIZE)
    return frames[voiced[start : start + width][: len(frames)]].reshape(-1)


async def _unbound(audio: np.ndarray) -> Match:
    raise RuntimeError("SourceProcessor.bind() not called")


def _return(obj):
    return obj


class _Identifier:
    async def identify(self, audio: np.ndarray, timeout: float) -> Match:
        raise NotImplementedError

    def close(self) -> None: ...


class _ThreadIdentifier(_Identifier):
    def __init__(self, matcher: Matcher, cap: int):
        self._matcher, self._cap, self._pending = matcher, cap, 0
        self._plock = threading.Lock()
        self._ex = ThreadPoolExecutor(1, thread_name_prefix="voiceroom-id")

    async def identify(self, audio: np.ndarray, timeout: float) -> Match:
        with self._plock:  # count unfinished work, including a timed-out call still running
            if self._pending >= self._cap:
                raise RuntimeError("identify backlog full")
            self._pending += 1
        cf = self._ex.submit(self._matcher.identify, audio)
        cf.add_done_callback(self._done)
        return await asyncio.wait_for(asyncio.wrap_future(cf), timeout)  # cancels if still queued

    def _done(self, _) -> None:
        with self._plock:
            self._pending -= 1

    def close(self) -> None:
        self._ex.shutdown(wait=False, cancel_futures=True)


def _serve(factory: Callable[[], Matcher], conn) -> None:
    """Child process: build the matcher, then answer one audio array at a time."""
    try:
        matcher = factory()
        conn.send("ready")
        while True:
            audio = conn.recv()
            try:
                conn.send(matcher.identify(audio))
            except Exception:
                conn.send(None)  # fixed marker: no exception payloads cross the pipe
    except (EOFError, KeyboardInterrupt, BrokenPipeError):
        pass


class _ProcIdentifier(_Identifier):
    """Matcher in a spawned child process; a timeout kills the child (respawned on demand).

    One deadline covers the whole request (send + compute + reply). Sending runs on a helper
    thread because a child that stops reading would block a large pipe write; killing the child
    makes that write fail, so no thread outlives it.
    """

    def __init__(self, factory: Callable[[], Matcher], start_timeout_s: float):
        self._factory, self._start_timeout = factory, start_timeout_s
        self._proc = None
        self._conn = None
        self._lock = asyncio.Lock()
        self._io = ThreadPoolExecutor(1, thread_name_prefix="voiceroom-pipe")
        self.kills = 0

    async def _recv(self, timeout: float):
        loop = asyncio.get_running_loop()
        conn = self._conn
        fut: asyncio.Future[None] = loop.create_future()
        loop.add_reader(conn.fileno(), lambda: fut.done() or fut.set_result(None))
        try:
            await asyncio.wait_for(fut, max(0.0, timeout))
        finally:
            loop.remove_reader(conn.fileno())
        return conn.recv()  # replies are tiny; the child has already written one

    async def identify(self, audio: np.ndarray, timeout: float) -> Match:
        loop = asyncio.get_running_loop()
        async with self._lock:
            deadline = loop.time() + timeout
            try:
                if self._proc is None:
                    ctx = mp.get_context("spawn")
                    self._conn, child = ctx.Pipe()
                    self._proc = ctx.Process(
                        target=_serve, args=(self._factory, child), daemon=True
                    )
                    self._proc.start()
                    child.close()
                    if await self._recv(self._start_timeout) != "ready":
                        raise RuntimeError("matcher child failed to start")
                    deadline = loop.time() + timeout  # start-up has its own budget
                send = loop.run_in_executor(self._io, self._conn.send, audio)
                await asyncio.wait_for(send, max(0.0, deadline - loop.time()))
                out = await self._recv(deadline - loop.time())
            except BaseException:
                self._kill()  # hung, crashed, or cancelled: never reuse a child mid-request
                raise
            if out is None:
                raise RuntimeError("identify failed in child")
            return out

    def _kill(self, count: bool = True) -> None:
        if self._proc is not None:
            self.kills += count
            self._proc.kill()  # first: this also fails any pipe write blocked in the I/O thread
            self._proc.join(timeout=5)
        if self._conn is not None:
            self._conn.close()
        self._proc = self._conn = None

    def close(self) -> None:
        self._kill(count=False)  # an orderly close is not a timeout kill
        self._io.shutdown(wait=False, cancel_futures=True)
