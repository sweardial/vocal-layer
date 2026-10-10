"""RoomAgent: drives a RoomPipeline from a RoomTransport and plays provider audio into the room."""

import asyncio
import logging
from collections.abc import AsyncIterator

import numpy as np

from voiceroom.adapters.base import AudioOut, Provider
from voiceroom.room import RoomPipeline
from voiceroom.rooms.base import ParticipantJoined, ParticipantLeft, RoomTransport, TrackAudio
from voiceroom.vad import FRAME_SIZE

log = logging.getLogger(__name__)

IDENTITY_ATTRIBUTE = "voiceroom_user"  # overrides the participant identity when set


class _PumpingProvider:
    """Wraps the provider so reply audio is pumped to the room once it is connected
    (`RoomPipeline.run()` connects and closes the provider)."""

    def __init__(self, inner: Provider, transport: RoomTransport):
        self.inner, self.transport = inner, transport
        self.pump: asyncio.Task | None = None

    async def connect(self) -> None:
        await self.inner.connect()
        self.pump = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        async for chunk in self.inner.audio_out():
            try:
                await self.transport.publish_audio(chunk)
            except Exception:  # keep draining so the provider's queue cannot grow unbounded
                log.warning("publish_audio failed; chunk dropped")

    async def send_turn(self, audio16k: np.ndarray, speaker_label: str | None) -> None:
        await self.inner.send_turn(audio16k, speaker_label)

    async def interrupt(self, played_ms: int | None = None) -> None:
        await self.inner.interrupt(played_ms)

    def audio_out(self) -> AsyncIterator[AudioOut]:
        return self.inner.audio_out()

    async def close(self) -> None:
        await self.inner.close()  # ends audio_out after the queued chunks
        if self.pump is not None:
            await self.pump


class RoomAgent:
    def __init__(self, transport: RoomTransport, room_pipeline: RoomPipeline, provider: Provider):
        self.transport, self.room = transport, room_pipeline
        self._wrapped = _PumpingProvider(provider, transport)
        self.room.provider = self._wrapped
        self._joined: dict[str, ParticipantJoined] = {}
        self._drains: set[asyncio.Task] = set()

    async def run(self) -> None:
        """Run until the transport's event stream ends, then drain queued turns and replies."""
        await self.transport.connect()
        room_task = asyncio.create_task(self.room.run())
        events_task = asyncio.create_task(self._handle_events())
        try:
            await asyncio.wait([room_task, events_task], return_when=asyncio.FIRST_COMPLETED)
            if room_task.done():  # the pipeline stopped (e.g. provider connect failed)
                room_task.result()
                return
            events_task.result()
            self.room.finish()
            await room_task
        finally:
            for t in (events_task, room_task):
                t.cancel()
            await asyncio.gather(events_task, room_task, return_exceptions=True)
            await self.transport.close()

    async def _handle_events(self) -> None:
        async for ev in self.transport.events():
            if isinstance(ev, ParticipantJoined):
                self._joined[ev.id] = ev
            elif isinstance(ev, TrackAudio):
                self._on_track(ev)
            elif isinstance(ev, ParticipantLeft):
                self._joined.pop(ev.id, None)
                self.room.remove_source(ev.id)

    def _on_track(self, ev: TrackAudio) -> None:
        info = self._joined.get(ev.participant_id)
        if info is None:
            log.warning("track for unknown participant ignored")
            self._discard(ev)
            return
        if ev.participant_id in self.room.sources:  # republished mic: the new track replaces it
            self.room.remove_source(ev.participant_id)
        claimed = info.attributes.get(IDENTITY_ATTRIBUTE) or info.identity
        self.room.add_source(ev.participant_id, _reframe(ev.frames), claimed)

    def _discard(self, ev: TrackAudio) -> None:
        async def drain() -> None:
            async for _ in ev.frames:
                pass

        self._drains.add(t := asyncio.create_task(drain()))
        t.add_done_callback(self._drains.discard)


async def _reframe(chunks: AsyncIterator[np.ndarray]) -> AsyncIterator[np.ndarray]:
    """Re-chunk arbitrary-length audio into FRAME_SIZE frames (the tail is zero-padded)."""
    buf = np.zeros(0, np.float32)
    async for c in chunks:
        buf = np.concatenate([buf, np.asarray(c, np.float32)])
        while len(buf) >= FRAME_SIZE:
            yield buf[:FRAME_SIZE]
            buf = buf[FRAME_SIZE:]
    if len(buf):
        yield np.pad(buf, (0, FRAME_SIZE - len(buf)))
