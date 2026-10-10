"""Room transport interface: no vendor types, 16 kHz mono float32 audio in both directions."""

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from voiceroom.adapters.base import AudioOut


@dataclass(frozen=True)
class ParticipantJoined:
    id: str  # transport-unique participant id
    identity: str | None = None  # the identity the room vouches for (claimed, not voice-verified)
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TrackAudio:
    """A participant's microphone track. `frames` yields 1-D float32 chunks at 16 kHz (any
    length) and ends when the track ends."""

    participant_id: str
    frames: AsyncIterator[np.ndarray]


@dataclass(frozen=True)
class ParticipantLeft:
    id: str


RoomEvent = ParticipantJoined | TrackAudio | ParticipantLeft


class RoomTransport(Protocol):
    async def connect(self) -> None: ...

    def events(self) -> AsyncIterator[RoomEvent]:
        """Room events in order (single consumer); a participant's `TrackAudio` follows its
        `ParticipantJoined`. The iterator ends when the room ends or after `close()`."""
        ...

    async def publish_audio(self, audio: AudioOut) -> None:
        """Play a chunk of the agent's reply audio into the room."""
        ...

    def audio_active(self) -> bool:
        """A reply is playing in the room, or still arriving (its final chunk not yet published)."""
        ...

    def interrupt_audio(self) -> int:
        """Stop publishing the current reply and flush the transport's audio buffer. Returns ms
        of the reply actually played into the room; later chunks of that reply are dropped."""
        ...

    async def close(self) -> None: ...


class PlaybackClock:
    """Tracks what part of the current reply has been published and played (wall clock).

    Playback is assumed to run at real time from the first published sample, which holds for
    a transport with a bounded buffer that paces `publish_audio`.
    """

    def __init__(self) -> None:
        self.cancelled = -1  # replies with id <= this were interrupted
        self._reply = -1
        self._start = 0.0
        self._end = 0.0  # when everything published so far has been played
        self._pub_s = 0.0
        self._final = True

    def accept(self, reply_id: int) -> bool:
        """False if the chunk's reply was interrupted; else it becomes the current reply."""
        if reply_id <= self.cancelled:
            return False
        if reply_id != self._reply:
            self._reply, self._pub_s, self._final = reply_id, 0.0, False
            self._start = self._end = time.monotonic()
        return True

    def add(self, seconds: float, final: bool = False) -> None:
        now = time.monotonic()
        if self._pub_s == 0.0:
            self._start = now
        self._end = max(self._end, now) + seconds
        self._pub_s += seconds
        self._final = self._final or final

    def active(self) -> bool:
        return self._reply > self.cancelled and (not self._final or time.monotonic() < self._end)

    def interrupt(self) -> int:
        now = time.monotonic()  # published minus what is still queued (pauses are not played)
        played = max(0.0, min(self._pub_s - max(0.0, self._end - now), now - self._start))
        self.cancelled = max(self.cancelled, self._reply)
        self._pub_s, self._final = 0.0, True
        return int(played * 1000)
