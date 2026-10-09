"""Streaming pipeline: frames -> VAD -> segmenter -> identify -> policy -> provider."""

import asyncio
import logging
import multiprocessing as mp
import threading
import time
from collections.abc import AsyncIterable, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from .adapters.base import Provider
from .config import MIN_DECISION_S
from .matcher import Match, Matcher
from .policy import Decision, IsolationPolicy, Verdict
from .segmenter import IdentifyReady, Segmenter, SpeechStart, TurnEnd
from .vad import VAD

log = logging.getLogger(__name__)

MAX_PENDING_TURNS = 4  # turns waiting for identify/commit; further turns are dropped
SEND_TIMEOUT_S = 5.0  # a provider send slower than this drops the turn
REMAINDER_MIN_S = 0.3  # audio after the early check that can be verified on its own
REMAINDER_SLACK_S = 0.032  # one frame of speech past the early check is tolerated
IDENTIFY_TIMEOUT_S = 5.0  # an identification slower than this drops the turn
MAX_INFLIGHT_IDENTIFY = 6  # submitted-but-unfinished identifications (running or queued)


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
        policy: IsolationPolicy,
        provider: Provider,
        max_pending: int = MAX_PENDING_TURNS,
        send_timeout_s: float = SEND_TIMEOUT_S,
        identify_timeout_s: float = IDENTIFY_TIMEOUT_S,
        max_inflight_identify: int = MAX_INFLIGHT_IDENTIFY,
        matcher_factory: Callable[[], Matcher] | None = None,
    ):
        """`matcher_factory` (picklable, module-level) runs identification in a child process
        that is killed on timeout, so a hung backend can neither leak audio nor block exit.
        Without it identification runs on a thread, which a hang cannot be killed from."""
        self.matcher_factory = matcher_factory
        self.max_pending, self.send_timeout_s = max_pending, send_timeout_s
        self.identify_timeout_s, self.max_inflight = identify_timeout_s, max_inflight_identify
        self._inflight = 0
        self._lock = threading.Lock()
        self.peak_inflight = 0
        self.vad, self.segmenter, self.matcher = vad, segmenter, matcher
        self.policy, self.provider = policy, provider
        self.records: list[TurnRecord] = []

    async def run(self, frame_source: AsyncIterable[np.ndarray] | Iterable[np.ndarray]) -> None:
        """Consume 512-sample frames until the source ends; commits accepted turns.

        Identification and commits run in a worker task, so the frame loop is never blocked by
        them; a failure on one turn drops that turn and the session continues.
        """
        self.vad.reset()
        self.segmenter.reset()
        self._ident: _Identifier = (
            _ProcIdentifier(self.matcher_factory)
            if self.matcher_factory
            else _ThreadIdentifier(self.matcher, self.max_inflight)
        )
        self._inflight = 0
        await self.provider.connect()
        early: asyncio.Task[Match] | None = None
        early_len = 0
        queue: asyncio.Queue[tuple | None] = asyncio.Queue(self.max_pending)
        worker = asyncio.create_task(self._worker(queue))
        stream_t = 0.0  # stream time of the next frame's start

        def handle(events: list, now: float) -> None:
            nonlocal early, early_len
            for ev in events:
                if isinstance(ev, SpeechStart):
                    early = None
                elif isinstance(ev, IdentifyReady):
                    early_len = len(ev.audio)
                    early = None
                    if queue.qsize() < self.max_pending:  # else the turn will overflow anyway
                        early = asyncio.create_task(self._identify(ev.audio))
                elif isinstance(ev, TurnEnd):
                    try:
                        queue.put_nowait((ev, early, early_len, now, time.perf_counter()))
                    except asyncio.QueueFull:  # never block frame ingestion
                        if early is not None:
                            early.cancel()
                        self._record_error(ev, "overflow", now, time.perf_counter())
                    early = None

        try:
            async for frame in _aiter(frame_source):
                prob = self.vad.process(frame)
                stream_t += len(frame) / 16000
                handle(self.segmenter.process(frame, prob), stream_t)
                await asyncio.sleep(0)  # let the worker run
            handle(self.segmenter.flush(), stream_t)
            await queue.put(None)  # bounded: waits only for in-flight turns (sends time out)
            await worker
        finally:
            worker.cancel()
            if early is not None:
                early.cancel()
            self._ident.close()
            await self.provider.close()

    async def _worker(self, queue: "asyncio.Queue[tuple | None]") -> None:
        while (item := await queue.get()) is not None:
            ev, now, t0 = item[0], item[3], item[4]
            try:
                await self._finish(*item)
            except Exception as exc:
                # Fixed code only: exception payloads may carry audio or embeddings.
                code = getattr(exc, "code", "turn_error")
                log.warning("turn dropped: %s", code)
                self._record_error(ev, code, now, t0)

    def _record_error(self, ev: TurnEnd, code: str, now: float, t0: float) -> None:
        self.records.append(
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
        """Run the matcher on the single identification thread, bounded in count and time."""
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

    async def _continuous(self, audio: np.ndarray, start: int) -> bool:
        """True if every 1 s window of audio[start:] (and the end of the turn) is the owner."""
        for w in _windows(audio, start):
            if (
                self.policy.decide(await self._identify(w), final=True).verdict
                is not Verdict.ACCEPT
            ):
                return False
        return True

    async def _finish(
        self,
        ev: TurnEnd,
        early: "asyncio.Task[Match] | None",
        early_len: int,
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
        if was_early and decision.verdict is Verdict.ACCEPT:
            # Audio after the early check must be verified (see _tail_checkable).
            extra = len(ev.audio) - ev.tail_samples - early_len  # speech only, no tail silence
            if extra > REMAINDER_SLACK_S * 16000 and not (
                _tail_checkable(extra) and await self._continuous(ev.audio, early_len)
            ):
                was_early = False
                decision = Decision(Verdict.REJECT)
        if decision is None or decision.verdict is Verdict.UNDECIDED:
            match = await self._identify(ev.audio)
            decision = self.policy.decide(match, final=True)
            if decision.verdict is Verdict.ACCEPT and not (
                _tail_checkable(int((ev.t_end - ev.t_start - MIN_DECISION_S) * 16000))
                and await self._continuous(ev.audio, 0)
            ):
                decision = Decision(Verdict.REJECT)  # speaker change inside the turn
        assert match is not None
        if decision.verdict is Verdict.ACCEPT:
            try:
                await asyncio.wait_for(
                    self.provider.send_turn(ev.audio, decision.label), self.send_timeout_s
                )
            except TimeoutError as exc:
                raise _StageError("send_timeout") from exc
            except Exception as exc:
                raise _StageError("send_error") from exc
        compute_s = time.perf_counter() - t0
        self.records.append(
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


class _StageError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _tail_checkable(extra: int) -> bool:
    """`extra` samples of speech past the first MIN_DECISION_S (beyond tail silence) must be
    long enough to embed on their own; a shorter continuation cannot be told apart from a
    speaker change, so the turn is dropped."""
    return extra <= REMAINDER_SLACK_S * 16000 or extra >= REMAINDER_MIN_S * 16000


def _windows(audio: np.ndarray, start: int) -> list[np.ndarray]:
    """1 s windows over audio[start:], plus one anchored at the very end (catches short tails)."""
    n = int(MIN_DECISION_S * 16000)
    out = [audio[i : i + n] for i in range(start, len(audio) - n + 1, n)]
    if len(audio) >= n:
        out.append(audio[-n:])
    elif len(audio) > start:
        out.append(audio[start:])
    return out


async def _aiter(src):
    if hasattr(src, "__aiter__"):
        async for x in src:
            yield x
    else:
        for x in src:
            yield x


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


START_TIMEOUT_S = 60.0  # child process start-up (imports, model load)


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
    """Matcher in a spawned child process; a timeout kills the child (respawned on demand)."""

    def __init__(self, factory: Callable[[], Matcher]):
        self._factory = factory
        self._proc = None
        self._conn = None
        self._lock = asyncio.Lock()

    async def _recv(self, timeout: float):
        loop = asyncio.get_running_loop()
        conn = self._conn
        fut: asyncio.Future[None] = loop.create_future()
        loop.add_reader(conn.fileno(), lambda: fut.done() or fut.set_result(None))
        try:
            await asyncio.wait_for(fut, timeout)
        finally:
            loop.remove_reader(conn.fileno())
        return conn.recv()

    async def identify(self, audio: np.ndarray, timeout: float) -> Match:
        async with self._lock:
            try:
                if self._proc is None:
                    ctx = mp.get_context("spawn")
                    self._conn, child = ctx.Pipe()
                    self._proc = ctx.Process(
                        target=_serve, args=(self._factory, child), daemon=True
                    )
                    self._proc.start()
                    child.close()
                    if await self._recv(START_TIMEOUT_S) != "ready":
                        raise RuntimeError("matcher child failed to start")
                self._conn.send(audio)
                out = await self._recv(timeout)
            except BaseException:
                self._kill()  # hung, crashed, or cancelled: never reuse a child mid-request
                raise
            if out is None:
                raise RuntimeError("identify failed in child")
            return out

    def _kill(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.join(timeout=5)
        if self._conn is not None:
            self._conn.close()
        self._proc = self._conn = None

    def close(self) -> None:
        self._kill()
