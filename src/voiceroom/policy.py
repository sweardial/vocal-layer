"""Speaker policies: map a match to accept / reject / undecided for a turn."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

import numpy as np

from .config import FALLBACK_WINDOW_BAD_RUN, threshold_for, window_bad_run_for, window_drop_for
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
        score_scale: float = 1.0,
        window_bad_run: int | None = None,
    ):
        self.owner = owner
        self.score_scale = score_scale  # cosine-scale buffers x this = buffers in matcher units
        self.threshold = threshold
        self.early_score_buffer = early_score_buffer
        self.early_margin = early_margin
        self.window_buffer = window_buffer  # a window may score this far below the threshold
        # ... and this far below the turn's median owner score (None: calibrated per embedder)
        self.window_drop = window_drop_for(embedder) if window_drop is None else window_drop
        self.window_bad_run = (
            window_bad_run_for(embedder) if window_bad_run is None else window_bad_run
        )

    def bind_matcher(self, matcher) -> None:
        """Adopt the matcher's score scale (AS-norm z-units) for the cosine-scale buffers."""
        self.score_scale = matcher.score_scale

    def decide(self, match: Match, final: bool = True) -> Decision:
        k = self.score_scale
        is_owner = match.name == self.owner
        if final:
            return Decision(Verdict.ACCEPT, self.owner) if is_owner else Decision(Verdict.REJECT)
        owner_score = match.scores.get(self.owner, match.score if is_owner else float("-inf"))
        rivals = [s for n, s in match.scores.items() if n != self.owner]
        lead = owner_score - max(rivals, default=float("-inf"))
        if is_owner and owner_score >= self.threshold + self.early_score_buffer * k:
            if lead >= self.early_margin * k:
                return Decision(Verdict.ACCEPT, self.owner)
        elif not is_owner:
            if match.name is not None and -lead >= self.early_margin * k:
                return Decision(Verdict.REJECT)  # confidently someone else
            if owner_score <= self.threshold - self.early_score_buffer * k:
                return Decision(Verdict.REJECT)
        return Decision(Verdict.UNDECIDED)

    def _bad_run(self, matches: list, who: str, k: float) -> bool:
        """Windows (None = pause) are bad if `who` is not best or scores under the floor."""
        scores = [m.scores.get(who, float("-inf")) for m in matches if m is not None]
        if not scores:
            return False
        floor = max(
            self.threshold - self.window_buffer * k, float(np.median(scores)) - self.window_drop * k
        )
        bad = [
            None if m is None else (m.best != who or m.scores.get(who, float("-inf")) < floor)
            for m in matches
        ]
        foreign = any(m is not None and m.name not in (None, who) for m in matches)
        return foreign or _sustained(bad, self.window_bad_run or FALLBACK_WINDOW_BAD_RUN)

    def windows_ok(self, matches: list[Match], label: str | None = None) -> bool:
        """Speaker-continuity check over short windows of one accepted turn.

        A window is bad if the owner is not its best candidate or it dips well below the turn's
        typical owner score. Isolated bad windows are noise (0.5 s windows of one speaker are
        unreliable, D-015); `window_bad_run` consecutive ones mean someone else spoke.
        """
        return not self._bad_run(matches, self.owner, self.score_scale)


UNKNOWN_LABEL = "Unknown"


def _sustained(flags: list[bool | None], need: int) -> bool:
    """True if `need` consecutive flags are True, or every scored flag is.
    None marks an unscored pause window and breaks a run."""
    scored = [f for f in flags if f is not None]
    if scored and all(scored):
        return True  # nothing in the turn supports the speaker, pauses or not
    need = max(1, need)
    run = 0
    for f in flags:
        run = run + 1 if f else 0
        if run >= need:
            return True
    return False


class CollaborationPolicy:
    """Every enrolled speaker reaches the model, attributed by name.

    A turn matching nobody enrolled follows `unknown`: "drop" discards it, "label" forwards it
    as "Unknown". An early match decides only an enrolled speaker, and only when the score
    clears the threshold by `early_score_buffer` with a runner-up gap of `early_margin`;
    everything else (including unknowns) waits for the full turn.
    """

    def __init__(
        self,
        enrolled: Iterable[str],
        unknown: str = "drop",
        threshold: float | None = None,
        early_score_buffer: float = 0.1,
        early_margin: float = 0.05,
        window_buffer: float = 0.1,
        window_drop: float | None = None,
        embedder: str = "",
        score_scale: float | None = None,
        window_bad_run: int | None = None,
    ):
        if unknown not in ("drop", "label"):
            raise ValueError("unknown must be 'drop' or 'label'")
        self.enrolled = frozenset(enrolled)
        if not self.enrolled or UNKNOWN_LABEL in self.enrolled:
            raise ValueError(f"enrolled must be non-empty and not contain {UNKNOWN_LABEL!r}")
        self.unknown = unknown
        self.threshold = threshold  # None: bound to the matcher's by `bind_matcher`
        self.early_score_buffer = early_score_buffer
        self.early_margin = early_margin
        self.window_buffer = window_buffer
        self.window_drop = window_drop  # None: calibrated per embedder by `bind_matcher`
        self.window_bad_run = window_bad_run  # likewise
        self.score_scale = score_scale  # None: the matcher's, by `bind_matcher` (1.0 until then)
        self._default_threshold = threshold is None  # embedder default, not caller-chosen
        if embedder:
            self.window_drop = window_drop_for(embedder) if window_drop is None else window_drop
            if window_bad_run is None:
                self.window_bad_run = window_bad_run_for(embedder)
            self.threshold = threshold_for(embedder) if threshold is None else threshold

    def bind_matcher(self, matcher) -> None:
        """Fill unset threshold / window_drop from the matcher (called by the Pipeline)."""
        if self.threshold is None or (self._default_threshold and matcher.cohort is not None):
            self.threshold = matcher.threshold  # a cosine default must not meet z-scores
        if self.score_scale is None:
            self.score_scale = matcher.score_scale
        if self.window_drop is None:
            name = matcher.embedder.name if matcher.embedder is not None else ""
            self.window_drop = window_drop_for(name)
        if self.window_bad_run is None:
            name = matcher.embedder.name if matcher.embedder is not None else ""
            self.window_bad_run = window_bad_run_for(name)
        if self.window_bad_run < 1:
            raise ValueError("window_bad_run must be >= 1")

    def decide(self, match: Match, final: bool = True) -> Decision:
        k = self.score_scale or 1.0
        if match.name in self.enrolled:
            if final or (
                match.score >= self.threshold + self.early_score_buffer * k
                and match.margin >= self.early_margin * k
            ):
                return Decision(Verdict.ACCEPT, match.name)
            return Decision(Verdict.UNDECIDED)
        if not final:
            return Decision(Verdict.UNDECIDED)
        if self.unknown == "label":
            return Decision(Verdict.ACCEPT, UNKNOWN_LABEL)
        return Decision(Verdict.REJECT)

    def _bad_run(self, matches: list, who: str, k: float) -> bool:
        """Windows (None = pause) are bad if `who` is not best or scores under the floor."""
        scores = [m.scores.get(who, float("-inf")) for m in matches if m is not None]
        if not scores:
            return False
        floor = max(
            self.threshold - self.window_buffer * k, float(np.median(scores)) - self.window_drop * k
        )
        bad = [
            None if m is None else (m.best != who or m.scores.get(who, float("-inf")) < floor)
            for m in matches
        ]
        foreign = any(m is not None and m.name not in (None, who) for m in matches)
        return foreign or _sustained(bad, self.window_bad_run or FALLBACK_WINDOW_BAD_RUN)

    def windows_ok(self, matches: list[Match], label: str | None = None) -> bool:
        """Continuity over short windows of one accepted turn, against the turn's `label`.

        Enrolled label: like isolation, every window must best-match that speaker and not dip
        below the floor. "Unknown": no window may clearly match an enrolled speaker.
        """
        if label is None or not matches:
            return True
        if label == UNKNOWN_LABEL:
            flags = [None if m is None else m.name in self.enrolled for m in matches]
            return not _sustained(flags, self.window_bad_run or FALLBACK_WINDOW_BAD_RUN)
        return not self._bad_run(matches, label, self.score_scale or 1.0)
