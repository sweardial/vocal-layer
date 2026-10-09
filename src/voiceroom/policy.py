"""Speaker policies: map a match to accept / reject / undecided for a turn."""

from dataclasses import dataclass
from enum import Enum

import numpy as np

from .config import window_drop_for
from .matcher import Match


class Verdict(Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    UNDECIDED = "undecided"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    label: str | None = None  # speaker label to attach to an accepted turn


class IsolationPolicy:
    """Only the owner's turns reach the model; everyone else is dropped.

    An early (partial-audio) match decides only when clearly confident: the score must clear the
    matcher threshold by `early_score_buffer`, or, for a rejection, fall that far below it.
    Otherwise the verdict is UNDECIDED and the pipeline waits for the full turn.
    """

    def __init__(
        self,
        owner: str,
        threshold: float,
        early_score_buffer: float = 0.1,
        early_margin: float = 0.05,
        window_buffer: float = 0.1,
        window_drop: float | None = None,
        embedder: str = "",
    ):
        self.owner = owner
        self.threshold = threshold
        self.early_score_buffer = early_score_buffer
        self.early_margin = early_margin
        self.window_buffer = window_buffer  # a window may score this far below the threshold
        # ... and this far below the turn's median owner score (None: calibrated per embedder)
        self.window_drop = window_drop_for(embedder) if window_drop is None else window_drop

    def decide(self, match: Match, final: bool = True) -> Decision:
        is_owner = match.name == self.owner
        if final:
            return Decision(Verdict.ACCEPT, self.owner) if is_owner else Decision(Verdict.REJECT)
        owner_score = match.scores.get(self.owner, match.score if is_owner else float("-inf"))
        rivals = [s for n, s in match.scores.items() if n != self.owner]
        lead = owner_score - max(rivals, default=float("-inf"))
        if is_owner and owner_score >= self.threshold + self.early_score_buffer:
            if lead >= self.early_margin:
                return Decision(Verdict.ACCEPT, self.owner)
        elif not is_owner:
            if match.name is not None and -lead >= self.early_margin:
                return Decision(Verdict.REJECT)  # confidently someone else
            if owner_score <= self.threshold - self.early_score_buffer:
                return Decision(Verdict.REJECT)
        return Decision(Verdict.UNDECIDED)

    def windows_ok(self, matches: list[Match]) -> bool:
        """Speaker-continuity check over short windows of one accepted turn.

        Every window must have the owner as its best candidate and must not dip well below the
        turn's typical owner score; a dip means someone else spoke inside the turn.
        """
        scores = [m.scores.get(self.owner, float("-inf")) for m in matches]
        if not scores:
            return True
        floor = max(
            self.threshold - self.window_buffer, float(np.median(scores)) - self.window_drop
        )
        return all(
            m.best == self.owner and s >= floor for m, s in zip(matches, scores, strict=True)
        )
