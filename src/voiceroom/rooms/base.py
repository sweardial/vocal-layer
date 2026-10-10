"""Room transport interface: no vendor types, 16 kHz mono float32 audio in both directions."""

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

    async def close(self) -> None: ...
