"""Streaming turn segmenter: per-frame speech probabilities in, turn events out."""

import math
import sys
from collections import deque
from dataclasses import dataclass

import numpy as np

from .config import MIN_DECISION_S
from .vad import FRAME_S, FRAME_SIZE


@dataclass(frozen=True)
class SpeechStart:
    t: float  # start of the first speech frame (s, stream time)


@dataclass(frozen=True)
class IdentifyReady:
    audio: np.ndarray  # turn audio so far, including pre-roll
    t_start: float


@dataclass(frozen=True)
class TurnEnd:
    audio: np.ndarray  # includes pre-roll and a short tail
    t_start: float  # first speech frame
    t_end: float  # end of last speech frame
    split: bool = False  # True if cut by max_turn_s; speech continues in the next turn


Event = SpeechStart | IdentifyReady | TurnEnd


class Segmenter:
    """Hangover endpointing over VAD probabilities.

    A turn starts after `start_frames` consecutive speech frames and ends after `end_silence_s`
    of non-speech. Pre-roll audio is prepended; a turn longer than `max_turn_s` is split.
    Blips shorter than `start_frames` never produce events.
    """

    def __init__(
        self,
        threshold: float = 0.5,
        identify_after_s: float = MIN_DECISION_S,
        end_silence_s: float = 0.4,
        preroll_s: float = 0.2,
        tail_s: float = 0.1,
        max_turn_s: float = 15.0,
        start_frames: int = 2,
    ):
        self.threshold = threshold
        self.identify_after = math.ceil(identify_after_s / FRAME_S - 1e-9)
        self.end_silence = max(1, round(end_silence_s / FRAME_S))
        self.tail = min(round(tail_s / FRAME_S), self.end_silence)
        self.max_frames = sys.maxsize if math.isinf(max_turn_s) else round(max_turn_s / FRAME_S)
        self.start_frames = start_frames
        n_pre = max(round(preroll_s / FRAME_S), start_frames)
        self._preroll: deque[np.ndarray] = deque(maxlen=n_pre)
        self.reset()

    def reset(self) -> None:
        self._preroll.clear()
        self._frames: list[np.ndarray] = []  # active turn, incl. pre-roll
        self._active = False
        self._run = 0  # consecutive speech frames while idle
        self._silence = 0  # trailing non-speech frames while active
        self._speech = 0  # frames since turn start (speech onset)
        self._voiced = 0  # speech frames since turn start
        self._identified = False
        self._idx = 0  # global frame index of the next frame
        self._start_idx = 0
        self._last_speech_idx = 0

    def process(self, frame: np.ndarray, prob: float) -> list[Event]:
        if len(frame) != FRAME_SIZE:
            raise ValueError(f"expected {FRAME_SIZE} samples, got {len(frame)}")
        i = self._idx
        self._idx += 1
        speech = prob >= self.threshold
        out: list[Event] = []
        if not self._active:
            self._preroll.append(frame)
            self._run = self._run + 1 if speech else 0
            if self._run >= self.start_frames:
                self._begin(i - self._run + 1, out)
            return out
        self._frames.append(frame)
        self._speech += 1
        if speech:
            self._silence = 0
            self._voiced += 1
            self._last_speech_idx = i
        else:
            self._silence += 1
        if not self._identified and self._voiced >= self.identify_after:
            self._identified = True
            out.append(IdentifyReady(np.concatenate(self._frames), self._start_idx * FRAME_S))
        if self._silence >= self.end_silence:
            out.append(self._end(split=False))
        elif self._speech >= self.max_frames:
            out.append(self._end(split=True))
            if speech:  # resume on the next speech frame, without the start_frames delay
                self._run = self.start_frames - 1
        return out

    def flush(self) -> list[Event]:
        """End of stream: close any open turn."""
        out = [self._end(split=False)] if self._active else []
        self.reset()
        return out

    def _begin(self, start_idx: int, out: list[Event]) -> None:
        self._active = True
        self._frames = list(self._preroll)
        self._preroll.clear()
        self._start_idx = start_idx
        self._last_speech_idx = self._idx - 1
        self._speech = self._idx - start_idx
        self._voiced = self._run
        self._silence = 0
        self._run = 0
        self._identified = False
        out.append(SpeechStart(start_idx * FRAME_S))

    def _end(self, split: bool) -> TurnEnd:
        keep_to = self._last_speech_idx + 1 + self.tail  # exclusive global frame index
        n_total = self._idx - (self._start_idx - self._n_preroll())
        first = self._start_idx - self._n_preroll()
        n = min(n_total, keep_to - first)
        audio = np.concatenate(self._frames[:n])
        ev = TurnEnd(audio, self._start_idx * FRAME_S, (self._last_speech_idx + 1) * FRAME_S, split)
        self._active = False
        self._frames = []
        self._silence = self._speech = self._voiced = 0
        return ev

    def _n_preroll(self) -> int:
        return len(self._frames) - (self._idx - self._start_idx)
