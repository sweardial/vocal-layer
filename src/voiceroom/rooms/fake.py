"""FakeRoom: scripted in-memory room. Participants join/leave at set room times and stream audio."""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import numpy as np

from voiceroom.adapters.base import AudioOut
from voiceroom.audio import load_audio
from voiceroom.rooms.base import (
    ParticipantJoined,
    ParticipantLeft,
    PlaybackClock,
    RoomEvent,
    TrackAudio,
)

SAMPLE_RATE = 16000
CHUNK = 512
QUEUE_DEPTH = 4  # chunks buffered per track: keeps the script's clock close to the consumers


@dataclass
class FakeParticipant:
    id: str
    audio: np.ndarray  # 16 kHz mono float32; streamed from `join_at`
    identity: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    join_at: float = 0.0  # room time, seconds
    leave_at: float | None = None  # None: leaves when its audio ends

    @classmethod
    def from_wav(cls, id: str, path: str, **kw) -> "FakeParticipant":
        return cls(id, load_audio(path), **kw)


class FakeRoom:
    def __init__(
        self,
        participants: list[FakeParticipant],
        realtime: bool = False,
        self_identity: str = "voiceroom-bot",
    ):
        self.self_identity = self_identity  # the agent's own identity (never a speaker)
        self.clock = PlaybackClock()
        self.flushes = 0
        self.participants = sorted(participants, key=lambda p: p.join_at)
        self.realtime = realtime  # pace chunks at wall-clock speed (else as fast as consumed)
        self.published: list[AudioOut] = []
        self.connected = False
        self.kicked: list[str] = []
        self.kicked_identities: list[str | None] = []
        self._events: asyncio.Queue[RoomEvent | None] = asyncio.Queue()
        self._driver: asyncio.Task | None = None

    async def connect(self) -> None:
        self.connected = True
        self._driver = asyncio.create_task(self._drive())

    async def _drive(self) -> None:
        """Advance room time one chunk at a time, joining and feeding participants."""
        pending = list(self.participants)
        live: list[tuple[FakeParticipant, asyncio.Queue, int, int]] = []  # p, q, pos, end
        tick = 0
        dt = CHUNK / SAMPLE_RATE
        start = time.monotonic()
        try:
            while pending or live:
                now = tick * dt
                while pending and pending[0].join_at <= now:
                    p = pending.pop(0)
                    q: asyncio.Queue = asyncio.Queue(QUEUE_DEPTH)
                    end = len(p.audio)
                    if p.leave_at is not None:
                        end = min(end, max(0, int((p.leave_at - p.join_at) * SAMPLE_RATE)))
                    self._events.put_nowait(ParticipantJoined(p.id, p.identity, dict(p.attributes)))
                    self._events.put_nowait(TrackAudio(p.id, _drain(q)))
                    live.append((p, q, 0, end))
                nxt = []
                for p, q, pos, end in live:
                    if p.id in self.kicked:  # removed by the agent: the track just stops
                        self._events.put_nowait(ParticipantLeft(p.id))
                    elif pos < end:
                        await q.put(p.audio[pos : min(pos + CHUNK, end)].astype(np.float32))
                        nxt.append((p, q, pos + CHUNK, end))
                    else:
                        if end < len(p.audio):  # leaves mid-stream: the track just stops
                            self._events.put_nowait(ParticipantLeft(p.id))
                        else:  # track ends, then the participant leaves
                            await q.put(None)
                            self._events.put_nowait(ParticipantLeft(p.id))
                live = nxt
                tick += 1
                # deadline pacing: sleeping a flat dt per chunk drifts slower than realtime
                await asyncio.sleep(
                    max(0.0, start + tick * dt - time.monotonic()) if self.realtime else 0
                )
        finally:
            self._events.put_nowait(None)

    async def events(self) -> AsyncIterator[RoomEvent]:
        while (ev := await self._events.get()) is not None:
            yield ev

    async def publish_audio(self, audio: AudioOut) -> None:
        if not self.clock.accept(audio.reply_id):
            return
        self.published.append(audio)
        self.clock.add(len(audio.samples) / audio.sample_rate, audio.final)

    def audio_active(self) -> bool:
        return self.clock.active()

    def interrupt_audio(self) -> int:
        self.flushes += 1
        return self.clock.interrupt()

    async def kick(self, participant_id: str, identity: str | None = None) -> None:
        """Remove a participant from the room (its stream stops, a ParticipantLeft follows)."""
        self.kicked.append(participant_id)
        self.kicked_identities.append(identity)

    async def close(self) -> None:
        self.connected = False
        if self._driver is not None:
            self._driver.cancel()
            await asyncio.gather(self._driver, return_exceptions=True)
            self._driver = None


async def _drain(q: asyncio.Queue) -> AsyncIterator[np.ndarray]:
    while (chunk := await q.get()) is not None:
        yield chunk
