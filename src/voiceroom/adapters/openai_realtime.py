"""OpenAI Realtime (GA) adapter over WebSocket. See D-007 and .work/tasks/T-019/plan.md.

We own turn-taking: server VAD is off, and each accepted turn is sent as
[attribution text item] -> input_audio_buffer.append... -> commit -> response.create.
"""

import asyncio
import base64
import contextlib
import itertools
import json
import logging
import os
import time
from collections import deque
from collections.abc import AsyncIterator

import numpy as np
from websockets.asyncio.client import ClientConnection, connect

from voiceroom.adapters.base import AudioOut
from voiceroom.audio import float_to_pcm16, pcm16_to_float, resample

log = logging.getLogger(__name__)
# websockets logs handshake headers (incl. Authorization) at DEBUG; keep those out of app logs.
_ws_log = logging.getLogger(__name__ + ".websockets")
_ws_log.propagate = False
_ws_log.addHandler(logging.NullHandler())

DEFAULT_MODEL = "gpt-realtime-2.1"
BASE_URL = "wss://api.openai.com/v1/realtime"
RATE = 24000  # the GA audio/pcm format only supports 24 kHz
CHUNK_SAMPLES = RATE // 10  # 100 ms per input_audio_buffer.append
MAX_BUFFER_S = 30  # unconsumed reply audio kept; beyond this the oldest chunks are dropped
MAX_BUFFER_ENTRIES = 256  # cap on queued chunks/markers, whatever their size
CANCEL_WAIT_S = 5.0  # max wait for a cancelled response to finish before giving up


def _encode_pcm16(x: np.ndarray) -> str:
    return base64.b64encode(float_to_pcm16(x).astype("<i2").tobytes()).decode("ascii")


def _decode_pcm16(b64: str) -> np.ndarray:
    return pcm16_to_float(np.frombuffer(base64.b64decode(b64), dtype="<i2"))


class OpenAIRealtimeProvider:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        voice: str = "marin",
        instructions: str | None = None,
        url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        connect_timeout_s: float = 10.0,
    ) -> None:
        self.model = model
        self.voice = voice
        self.instructions = instructions
        self._custom_url = url
        self._key_env = api_key_env
        self._timeout = connect_timeout_s
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._closing: asyncio.Task | None = None
        self._buf: deque[AudioOut | None] = deque()
        self._buf_ev = asyncio.Event()
        self._consumers = 0
        self._reply_id = 0
        self._req_ids = itertools.count(1)
        self._reset_responses()

    def _reset_responses(self) -> None:
        """Forget all connection-scoped response state."""
        self._pending: dict[str, bool] = {}  # response.create event_id -> cancelled (FIFO)
        self._open: dict[str, bool] = {}  # created, not yet done: response id -> dropped
        self._active: str | None = None  # response id whose audio we are yielding
        self._item: str | None = None  # last assistant audio item
        self._item_samples = 0  # samples received for it
        self._changed = asyncio.Event()

    def __repr__(self) -> str:
        return f"OpenAIRealtimeProvider(model={self.model!r}, connected={self._ws is not None})"

    async def connect(self) -> None:
        if self._ws is not None or self._reader is not None:
            await self.close()
        key = os.environ.get(self._key_env)
        if not key and not self._custom_url:
            raise RuntimeError(f"{self._key_env} is not set")
        url = self._custom_url or f"{BASE_URL}?model={self.model}"
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._buf.clear()
        self._consumers = 0
        self._reset_responses()
        self._ws = await connect(
            url, additional_headers=headers, open_timeout=self._timeout, logger=_ws_log
        )
        try:
            async with asyncio.timeout(self._timeout):
                await self._expect("session.created")
                await self._send(self._session_update())
                await self._expect("session.updated")
        except BaseException:
            await self._ws.close()
            self._ws = None
            raise
        self._reader = asyncio.create_task(self._read_loop())

    def _session_update(self) -> dict:
        session: dict = {
            "type": "realtime",
            "output_modalities": ["audio"],
            "audio": {
                "input": {"format": {"type": "audio/pcm", "rate": RATE}, "turn_detection": None},
                "output": {"format": {"type": "audio/pcm", "rate": RATE}, "voice": self.voice},
            },
        }
        if self.instructions:
            session["instructions"] = self.instructions
        return {"type": "session.update", "session": session}

    async def _expect(self, etype: str) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            ev = json.loads(raw)
            if ev.get("type") == etype:
                return
            if ev.get("type") == "error":
                raise RuntimeError(f"realtime error: {_err(ev)}")
        raise ConnectionError(f"closed before {etype}")

    async def _send(self, event: dict) -> None:
        assert self._ws is not None
        await self._ws.send(json.dumps(event))

    async def send_turn(self, audio16k: np.ndarray, speaker_label: str | None) -> None:
        if self._ws is None:
            raise RuntimeError("not connected")
        audio16k = np.asarray(audio16k)
        if audio16k.ndim != 1 or audio16k.size == 0 or audio16k.dtype != np.float32:
            raise ValueError("audio16k must be a non-empty 1-D float32 array")
        pcm = resample(audio16k, 16000, RATE)
        # Encode everything before the first send so a bad sample can't leave a partial turn.
        chunks = [
            _encode_pcm16(pcm[i : i + CHUNK_SAMPLES]) for i in range(0, len(pcm), CHUNK_SAMPLES)
        ]
        async with self._send_lock:
            # The API allows one response at a time; a new accepted turn supersedes the old reply.
            await self._cancel_locked()
            self._item = None
            sent_any = False
            eid = None
            try:
                if speaker_label is not None:
                    sent_any = True
                    await self._send(
                        {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": f"[Speaker: {speaker_label}]"}
                                ],
                            },
                        }
                    )
                for b64 in chunks:
                    sent_any = True
                    await self._send({"type": "input_audio_buffer.append", "audio": b64})
                await self._send({"type": "input_audio_buffer.commit"})
                eid = f"req_{next(self._req_ids)}"
                self._pending[eid] = False
                await self._send({"type": "response.create", "event_id": eid})
            except BaseException:
                if eid is not None:  # response.create may or may not have gone out: unknowable
                    self._abort()
                elif sent_any and self._ws is not None:  # don't leave a partial turn buffered
                    with contextlib.suppress(Exception):
                        await self._send({"type": "input_audio_buffer.clear"})
                raise

    async def _cancel_locked(self) -> None:
        """Cancel every response in progress or requested; wait until the server is idle."""
        if not self._pending and not self._open:
            return
        self._pending = dict.fromkeys(self._pending, True)
        self._open = dict.fromkeys(self._open, True)
        self._active = None
        self._purge_queue()
        sent_for: set[str] = set()
        last_cancel = -1e9
        try:
            async with asyncio.timeout(CANCEL_WAIT_S):
                while self._pending or self._open:
                    self._changed.clear()
                    fresh = set(self._open) - sent_for  # newly created responses need a cancel
                    if fresh or time.monotonic() - last_cancel >= 1.0:
                        sent_for |= fresh
                        last_cancel = time.monotonic()
                        await self._send({"type": "response.cancel"})
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._changed.wait(), 0.25)
        except TimeoutError:
            self._abort()
            raise ConnectionError("cancelled response did not finish; connection dropped") from None

    def _abort(self) -> None:
        """Drop the connection without awaiting (response state is unknowable); reconnect needed."""
        ws, self._ws = self._ws, None
        self._reset_responses()
        if self._reader is not None:
            reader, self._reader = self._reader, None
            reader.cancel()
        if ws is not None:
            self._closing = asyncio.create_task(ws.close())
        self._push(None)

    def _purge_queue(self) -> None:
        """Drop queued reply audio (it was cancelled); keep the end-of-stream sentinel."""
        sentinel = None in self._buf
        self._buf.clear()
        if sentinel:
            self._buf.append(None)

    def _push(self, chunk: AudioOut | None) -> None:
        self._buf.append(chunk)
        self._buf_ev.set()
        total = sum(len(c.samples) for c in self._buf if c is not None)
        if total <= MAX_BUFFER_S * RATE and len(self._buf) <= MAX_BUFFER_ENTRIES:
            return
        log.warning("reply audio buffer full; dropping oldest audio")
        n = len(self._buf)
        kept: deque[AudioOut | None] = deque()
        for c in self._buf:  # drop oldest chunks and markers; keep the end-of-stream sentinel
            if c is not None and (total > MAX_BUFFER_S * RATE or n > MAX_BUFFER_ENTRIES):
                total -= len(c.samples)
                n -= 1
            else:
                kept.append(c)
        self._buf = kept

    async def interrupt(self, played_ms: int | None = None) -> None:
        if self._ws is None:
            return
        async with self._send_lock:
            await self._cancel_locked()
            self._purge_queue()
            item, self._item = self._item, None
            if played_ms is not None and item is not None:
                received_ms = self._item_samples * 1000 // RATE
                await self._send(
                    {
                        "type": "conversation.item.truncate",
                        "item_id": item,
                        "content_index": 0,
                        "audio_end_ms": max(0, min(played_ms, received_ms)),
                    }
                )

    async def audio_out(self) -> AsyncIterator[AudioOut]:
        if self._consumers:
            raise RuntimeError("audio_out is single-consumer")
        self._consumers += 1
        while True:
            while not self._buf:
                self._buf_ev.clear()
                await self._buf_ev.wait()
            if (chunk := self._buf.popleft()) is None:
                return
            yield chunk

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        self._reset_responses()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        if ws is not None:
            await ws.close()
        self._push(None)

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    self._handle(json.loads(raw))
                except (KeyError, ValueError, TypeError) as e:  # bad event: skip it, keep going
                    log.warning("bad realtime event skipped: %s", type(e).__name__)
        except Exception as e:  # connection dropped; end the audio stream
            log.warning("realtime reader stopped: %s", type(e).__name__)
            self._ws = None
        finally:
            if self._reader is asyncio.current_task():  # not superseded by _abort/close
                self._push(None)

    def _handle(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "response.created":
            rid = ev["response"]["id"]
            cancelled = (
                self._pending.pop(next(iter(self._pending)), False) if self._pending else False
            )
            self._open[rid] = cancelled
            if not cancelled:
                self._active = rid
                self._reply_id += 1
                self._item, self._item_samples = None, 0
        elif t == "response.output_audio.delta":
            if self._open.get(ev.get("response_id")) is not False:
                return  # unknown or cancelled response
            self._item = ev.get("item_id", self._item)
            samples = _decode_pcm16(ev["delta"])
            self._item_samples += len(samples)
            self._push(AudioOut(samples, RATE, False, self._reply_id))
        elif t == "response.done":
            rid = ev.get("response", {}).get("id")
            if self._open.pop(rid, False):
                pass  # cancelled reply: no audio, no final
            else:
                if rid == self._active:
                    self._active = None
                status = ev.get("response", {}).get("status")
                if status != "completed":
                    log.warning("realtime response ended: %s", status)
                self._push(AudioOut(np.zeros(0, np.float32), RATE, True, self._reply_id))
        elif t == "error":
            if self._pending.pop(ev.get("error", {}).get("event_id"), None) is not None:
                log.warning("response.create rejected: %s", _err(ev))
            else:
                log.info("realtime error: %s", _err(ev))
        self._changed.set()


def _err(ev: dict) -> str:
    e = ev.get("error", {})
    return f"{e.get('type')}/{e.get('code')}: {e.get('message')}"
