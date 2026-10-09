"""Provider-agnostic async interface to a realtime voice model."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class AudioOut:
    """A chunk of reply audio: mono float32 in [-1, 1] at `sample_rate`."""

    samples: np.ndarray
    sample_rate: int = 16000
    final: bool = False  # last chunk of a reply
    reply_id: int = 0  # increments per reply; consumers drop ids <= the last interrupted one


class Provider(Protocol):
    """We own turn-taking: each call to `send_turn` is one complete, accepted turn."""

    async def connect(self) -> None: ...

    async def send_turn(self, audio16k: np.ndarray, speaker_label: str | None) -> None:
        """Commit one turn (1-D float32 at 16 kHz, non-empty) and request a reply."""
        ...

    async def interrupt(self, played_ms: int | None = None) -> None:
        """Cancel any reply in progress (barge-in).

        `played_ms`: ms of the current reply actually played out (None if unknown), so the
        provider's context can be truncated to what the user heard.
        """
        ...

    def audio_out(self) -> AsyncIterator[AudioOut]:
        """Reply audio (single consumer); the iterator ends after `close()`.

        Adapters must not yield chunks of a reply cancelled by `interrupt()`.
        """
        ...

    async def close(self) -> None: ...
