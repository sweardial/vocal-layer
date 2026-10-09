"""Streaming pipeline: frames -> VAD -> segmenter -> identify -> policy -> provider."""

import asyncio
import functools
import logging
import multiprocessing as mp
import pickle
import threading
import time
from collections import deque
from collections.abc import AsyncIterable, Callable, Iterable
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
        self.vad, self.segmenter, self.matcher = vad, segmenter, matcher
        self.policy, self.provider = policy, provider
        policy.bind_matcher(matcher)
        self.on_record = on_record
        self.sink = sink  # barge-in: an accepted turn during playback flushes it + interrupts
        self.records: deque[TurnRecord] = deque(maxlen=max_records)  # most recent run/turns

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
        self.vad.reset()
        self.segmenter.reset()
        self._ident = self._make_identifier()
        self.records.clear()
        self._inflight = 0
        await self.provider.connect()
        early: asyncio.Task[Match] | None = None
        wins: dict[
            int, asyncio.Task[Match] | None
        ] = {}  # window start frame -> score (None: pause)
        early_len = 0
        barge_tasks: set[asyncio.Task] = set()
        queue: asyncio.Queue[tuple | None] = asyncio.Queue(self.max_pending)
        worker = asyncio.create_task(self._worker(queue))
        stream_t = 0.0  # stream time of the next frame's start

        def cancel_all() -> None:
            nonlocal early
            if early is not None:
                early.cancel()
            for t in wins.values():
                if t is not None:
                    t.cancel()
            early = None
            wins.clear()

        def has_room() -> bool:
            return queue.qsize() < self.max_pending and self._inflight < self.max_inflight - 2

        def on_early(task: "asyncio.Task[Match]") -> None:
            # Speaker confirmed mid-turn: stop the reply now, not at the end of the turn.
            if task.cancelled() or task.exception() is not None or self.sink is None:
                return
            if self.policy.decide(task.result(), final=False).verdict is Verdict.ACCEPT:
                t = asyncio.create_task(self._barge_in())
                barge_tasks.add(t)
                t.add_done_callback(barge_tasks.discard)

        def handle(events: list, now: float) -> None:
            nonlocal early, early_len
            for ev in events:
                if isinstance(ev, SpeechStart):
                    cancel_all()
                elif isinstance(ev, IdentifyReady):
                    early_len = len(ev.audio)
                    if has_room():  # else the turn is decided at TurnEnd
                        early = self._spawn(ev.audio)
                        early.add_done_callback(on_early)
                elif isinstance(ev, TurnEnd):
                    try:
                        queue.put_nowait(
                            (ev, early, early_len, dict(wins), now, time.perf_counter())
                        )
                        early = None
                        wins.clear()
                    except asyncio.QueueFull:  # never block frame ingestion
                        cancel_all()
                        self._record_error(ev, "overflow", now, time.perf_counter())

        try:
            async for frame in _aiter(frame_source):
                prob = self.vad.process(frame)
                stream_t += len(frame) / 16000
                handle(self.segmenter.process(frame, prob), stream_t)
                # Score each ~0.5 s window of the open turn as soon as it is complete, so the
                # speaker-continuity check costs little at TurnEnd.
                n = self.segmenter.active_frames
                nxt = (max(wins) + HOP_FRAMES) if wins else 0
                if n >= nxt + WIN_FRAMES and has_room():
                    audio, voiced = self.segmenter.turn_audio(), self.segmenter.turn_voiced()
                    while n >= nxt + WIN_FRAMES:
                        if voiced[nxt : nxt + WIN_FRAMES].mean() >= MIN_VOICED:
                            wins[nxt] = self._spawn(_window(audio, voiced, nxt, WIN_FRAMES))
                        else:
                            wins[nxt] = None
                        nxt += HOP_FRAMES
                await asyncio.sleep(0)  # let the worker run
            handle(self.segmenter.flush(), stream_t)
            await queue.put(None)  # bounded: waits only for in-flight turns (sends time out)
            await worker
        finally:
            worker.cancel()
            for t in barge_tasks:
                t.cancel()
            cancel_all()
            self._ident.close()
            await self.provider.close()

    async def _worker(self, queue: "asyncio.Queue[tuple | None]") -> None:
        while (item := await queue.get()) is not None:
            ev, now, t0 = item[0], item[4], item[5]
            try:
                await self._finish(*item)
            except Exception as exc:
                # Fixed code only: exception payloads may carry audio or embeddings.
                code = getattr(exc, "code", "turn_error")
                log.warning("turn dropped: %s", code)
                self._record_error(ev, code, now, t0)
            finally:
                for t in item[3].values():
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

    def _spawn(self, audio: np.ndarray) -> "asyncio.Task[Match]":
        t = asyncio.create_task(self._identify(audio))
        t.add_done_callback(lambda t: t.cancelled() or t.exception())  # mark retrieved
        return t

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
                m = await self._identify(_window(ev.audio, voiced, st, width))
            matches.append(m)
        return self.policy.windows_ok(matches, label, short=short)

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

    async def _finish(
        self,
        ev: TurnEnd,
        early: "asyncio.Task[Match] | None",
        early_len: int,
        wins: "dict[int, asyncio.Task[Match] | None]",
        now: float,
        t0: float,
    ) -> None:
        """`t0`: perf_counter when the TurnEnd was emitted (includes any queueing delay)."""
        endpoint_s = max(0.0, now - ev.t_end)
        decision, match, was_early = None, None, False
        if early is not None:
            try:
                match = await early
            except Exception:  # early check is an optimisation: fall back to the full turn
                match = None
        if match is not None:
            decision = self.policy.decide(match, final=False)
            was_early = decision.verdict is not Verdict.UNDECIDED
        if decision is None or decision.verdict is Verdict.UNDECIDED:
            match = await self._identify(ev.audio)
            decision = self.policy.decide(match, final=True)
        assert match is not None and decision is not None
        if decision.verdict is Verdict.ACCEPT and not await self._continuity_ok(
            ev, wins, decision.label
        ):
            decision, was_early = Decision(Verdict.REJECT), False  # speaker change inside the turn
        if decision.verdict is Verdict.ACCEPT:
            await self._barge_in()
            try:
                await asyncio.wait_for(
                    self.provider.send_turn(ev.audio, decision.label), self.send_timeout_s
                )
            except TimeoutError as exc:
                raise _StageError("send_timeout") from exc
            except Exception as exc:
                raise _StageError("send_error") from exc
        compute_s = time.perf_counter() - t0
        self._emit(
            TurnRecord(
                ev.t_start,
                ev.t_end,
                decision.verdict,
                match.name,
                match.score,
                was_early,
                endpoint_s,
                compute_s,
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
