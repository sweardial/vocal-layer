"""MockProvider: records committed turns and answers each with a short synthetic tone."""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import numpy as np

from voiceroom.adapters.base import AudioOut

REPLY_S = 0.3
REPLY_CHUNKS = 3
REPLY_HZ = 440.0


@dataclass(frozen=True)
class RecordedTurn:
    audio: np.ndarray
    speaker_label: str | None
    t: float  # time.monotonic() at commit


class MockProvider:
    def __init__(self, sample_rate: int = 16000, reply_s: float = REPLY_S) -> None:
        self.reply_s = reply_s
        self.sample_rate = sample_rate
        self.turns: list[RecordedTurn] = []
        self.interrupts: list[int | None] = []  # played_ms per interrupt
        self._reply_id = 0
        self._closed = False
        self.connected = False
        self._closed = True
        self._q: asyncio.Queue[AudioOut | None] = asyncio.Queue()
        self._consumers = 0

    async def connect(self) -> None:
        self.connected = True
        self._closed = False
        self._q = asyncio.Queue()
        self._consumers = 0

    async def send_turn(self, audio16k: np.ndarray, speaker_label: str | None) -> None:
        if not self.connected:
            raise RuntimeError("not connected")
        audio16k = np.asarray(audio16k)
        if audio16k.ndim != 1 or audio16k.size == 0 or audio16k.dtype != np.float32:
            raise ValueError("audio16k must be a non-empty 1-D float32 array")
        self.turns.append(RecordedTurn(audio16k, speaker_label, time.monotonic()))
        n = int(self.reply_s * self.sample_rate)
        t = np.arange(n) / self.sample_rate
        tone = (0.1 * np.sin(2 * np.pi * REPLY_HZ * t)).astype(np.float32)
        parts = np.array_split(tone, REPLY_CHUNKS)
        self._reply_id += 1
        # Chunks are queued up front, so interrupt() drops the unread remainder.
        for i, p in enumerate(parts):
            self._q.put_nowait(AudioOut(p, self.sample_rate, i == REPLY_CHUNKS - 1, self._reply_id))

    async def interrupt(self, played_ms: int | None = None) -> None:
        if self._closed:
            return
        self.interrupts.append(played_ms)
        while not self._q.empty():
            self._q.get_nowait()

    async def audio_out(self) -> AsyncIterator[AudioOut]:
        if self._consumers:
            raise RuntimeError("audio_out is single-consumer")
        self._consumers += 1
        while (chunk := await self._q.get()) is not None:
            yield chunk

    async def close(self) -> None:
        self.connected = False
        self._closed = True
        self._q.put_nowait(None)
