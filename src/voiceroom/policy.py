"""Speaker policies: map a match to accept / reject / undecided for a turn."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

import numpy as np

from .config import (
    DEEP_BAD_RUN,
    DEEP_BUFFER,
    FALLBACK_WINDOW_BAD_RUN,
    TRACK_ON_MISMATCH,
    VERIFY_THRESHOLD_RELAX,
    threshold_for,
    window_bad_run_for,
    window_drop_for,
)
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

    def _bad_run(self, matches: list, who: str, k: float, short: bool = False) -> bool:
        return _continuity_bad(self, matches, who, k, short)

    def windows_ok(
        self, matches: list[Match], label: str | None = None, short: bool = False
    ) -> bool:
        """Speaker-continuity check over short windows of one accepted turn.

        `short` (a turn under `CONTINUITY_MIN_S`): too few windows to judge a doubtful run, so
        only two consecutive windows naming another enrolled speaker, or two under the
        floor (the long rule's: threshold - buffer or turn median - drop), reject.

        A window is bad if the owner is not its best candidate or it dips well below the turn's
        typical owner score. Isolated bad windows are noise (0.5 s windows of one speaker are
        unreliable, D-015); `window_bad_run` consecutive ones mean someone else spoke.
        """
        return self.windows_rule(matches, label, short) is None

    def windows_rule(
        self, matches: list[Match], label: str | None = None, short: bool = False
    ) -> str | None:
        """Like `windows_ok` but returns the name of the rule that rejects (None = ok)."""
        return _continuity_rule(self, matches, self.owner, self.score_scale, short)

    def describe_windows(self, matches: list, label: str | None, short: bool, rule: str) -> str:
        return describe_windows(self, matches, self.owner, short, rule)


UNKNOWN_LABEL = "Unknown"


def _run(flags: list[bool], need: int) -> bool:
    run = 0
    for f in flags:
        run = run + 1 if f else 0
        if run >= need:
            return True
    return False


def _floor(self, matches: list, who: str, k: float) -> float | None:
    scores = [m.scores.get(who, float("-inf")) for m in matches if m is not None]
    if not scores:
        return None
    return max(
        self.threshold - self.window_buffer * k, float(np.median(scores)) - self.window_drop * k
    )


def _continuity_bad(self, matches: list, who: str, k: float, short: bool) -> bool:
    return _continuity_rule(self, matches, who, k, short) is not None


def describe_windows(self, matches: list, who: str, short: bool, rule: str) -> str:
    """One log line: the rule that rejected a turn and the window numbers (no audio/embeddings)."""
    k = getattr(self, "score_scale", None) or 1.0
    floor = _floor(self, matches, who, k)
    wins = [
        "pause"
        if m is None
        else f"{m.scores.get(who, float('-inf')):.2f}/{m.name or m.best or '-'}"
        for m in matches
    ]
    f = "n/a" if floor is None else f"{floor:.2f}"
    return (
        f"continuity reject: rule={rule} who={who} short={short} thr={self.threshold:.2f} "
        f"floor={f} windows(score/name)=[{' '.join(wins)}]"
    )


def _continuity_rule(self, matches: list, who: str, k: float, short: bool) -> str | None:
    """Name of the rule that rejects the windows (None = ok). Windows are None for pauses."""
    floor = _floor(self, matches, who, k)
    if floor is None:
        return None
    bad = [
        None if m is None else (m.best != who or m.scores.get(who, float("-inf")) < floor)
        for m in matches
    ]
    # clearly someone else (not best, far under the threshold): needs a shorter run than `bad`
    deep = [
        None
        if m is None
        else (m.best != who and m.scores.get(who, float("-inf")) < self.threshold - DEEP_BUFFER * k)
        for m in matches
    ]
    foreign = any(m is not None and m.name not in (None, who) for m in matches)
    if short:  # a lone odd window proves little: two foreign / under-floor in a row
        others = [m is not None and m.name not in (None, who) for m in matches]
        need = getattr(self, "short_foreign_run", 2)
        low = [
            m is not None and m.scores.get(who, float("-inf")) < floor for m in matches
        ]  # same floor as `bad`, no rival needed (owner-only enrollment)
        if _run(others, need):
            return "short-foreign-run"
        return "short-low-run" if _run(low, 2) else None
    if foreign:
        return "foreign-named"
    scored = [f for f in bad if f is not None]
    if _sustained(bad, self.window_bad_run or FALLBACK_WINDOW_BAD_RUN):
        return "all-bad" if scored and all(scored) else "bad-run"
    return "deep-run" if _sustained(deep, DEEP_BAD_RUN) else None


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
    short_foreign_run = 1  # attribution: one named foreign window still rejects a short turn

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

    def _bad_run(self, matches: list, who: str, k: float, short: bool = False) -> bool:
        return _continuity_bad(self, matches, who, k, short)

    def windows_ok(
        self, matches: list[Match], label: str | None = None, short: bool = False
    ) -> bool:
        """Continuity over short windows of one accepted turn, against the turn's `label`.

        Enrolled label: like isolation, every window must best-match that speaker and not dip
        below the floor. "Unknown": no window may clearly match an enrolled speaker.
        """
        return self.windows_rule(matches, label, short) is None

    def windows_rule(
        self, matches: list[Match], label: str | None = None, short: bool = False
    ) -> str | None:
        """Like `windows_ok` but returns the name of the rule that rejects (None = ok)."""
        if label is None or not matches:
            return None
        if label == UNKNOWN_LABEL:
            flags = [None if m is None else m.name in self.enrolled for m in matches]
            bad = _sustained(flags, self.window_bad_run or FALLBACK_WINDOW_BAD_RUN)
            return "unknown-matches-enrolled" if bad else None
        return _continuity_rule(self, matches, label, self.score_scale or 1.0, short)

    def describe_windows(self, matches: list, label: str | None, short: bool, rule: str) -> str:
        return describe_windows(self, matches, label or "-", short, rule)


class TrackPolicy:
    """Identity policy for one room track (a participant's audio source).

    `verified`: the track claims `claimed`; check the turn against that member's signature only
    (1:1, accept at `verify_threshold`). A confident match to *another* member is a mismatch:
    `on_mismatch` "relabel" forwards it under that member's name, "drop" discards it. Anything
    else is unknown and follows `unknown` ("drop" / "label"). Continuity always uses the
    lenient short-turn rule (per-person mics give short turns; the track already narrows who).

    `shared` (one mic, several people): plain CollaborationPolicy over `members`.
    """

    def __init__(
        self,
        mode: str,
        members: Iterable[str],
        claimed: str | None = None,
        unknown: str = "drop",
        on_mismatch: str = TRACK_ON_MISMATCH,
        threshold: float | None = None,
        verify_threshold: float | None = None,
        early_score_buffer: float = 0.1,
        early_margin: float = 0.05,
        embedder: str = "",
        **kw,
    ):
        if mode not in ("verified", "shared"):
            raise ValueError("mode must be 'verified' or 'shared'")
        if on_mismatch not in ("relabel", "drop"):
            raise ValueError("on_mismatch must be 'relabel' or 'drop'")
        self.mode, self.members = mode, frozenset(members)
        self.claimed, self.unknown, self.on_mismatch = claimed, unknown, on_mismatch
        if mode == "verified" and claimed not in self.members:
            raise ValueError("verified track needs a claimed identity that is a session member")
        self._collab = CollaborationPolicy(
            self.members, unknown, threshold, early_score_buffer, early_margin,
            embedder=embedder, **kw,
        )  # fmt: skip
        self.verify_threshold = verify_threshold
        self._relax_default = verify_threshold is None
        self.early_score_buffer, self.early_margin = early_score_buffer, early_margin
        if embedder and self._relax_default:
            self.verify_threshold = threshold_for(embedder) - VERIFY_THRESHOLD_RELAX

    # the collaboration policy owns thresholds / scale / window calibration
    @property
    def threshold(self):
        return self._collab.threshold

    @property
    def score_scale(self):
        return self._collab.score_scale or 1.0

    def bind_matcher(self, matcher) -> None:
        self._collab.bind_matcher(matcher)
        if self._relax_default:  # relaxed from the (possibly AS-norm) identification threshold
            self.verify_threshold = self.threshold - VERIFY_THRESHOLD_RELAX * self.score_scale

    def decide(self, match: Match, final: bool = True) -> Decision:
        if self.mode == "shared":
            return self._collab.decide(match, final)
        k = self.score_scale
        mine = match.scores.get(self.claimed, float("-inf"))
        other = match.name in self.members and match.name != self.claimed
        if not final:  # early: only a clear, uncontested claimed match
            clear = mine >= self.verify_threshold + self.early_score_buffer * k
            if clear and match.best == self.claimed and match.margin >= self.early_margin * k:
                return Decision(Verdict.ACCEPT, self.claimed)
            return Decision(Verdict.UNDECIDED)
        if other:
            if self.on_mismatch == "relabel":
                return Decision(Verdict.ACCEPT, match.name)
            return Decision(Verdict.REJECT)
        # confidently someone else who is not a session member (e.g. revoked): never the owner
        confident_other = match.name is not None and match.name != self.claimed
        if mine >= self.verify_threshold and not confident_other:
            return Decision(Verdict.ACCEPT, self.claimed)
        if self.unknown == "label":
            return Decision(Verdict.ACCEPT, UNKNOWN_LABEL)
        return Decision(Verdict.REJECT)

    def windows_ok(
        self, matches: list[Match], label: str | None = None, short: bool = False
    ) -> bool:
        return self.windows_rule(matches, label, short) is None

    def windows_rule(
        self, matches: list[Match], label: str | None = None, short: bool = False
    ) -> str | None:
        if self.mode == "shared":
            return self._collab.windows_rule(matches, label, short)
        if label is None or not matches:
            return None
        if label == UNKNOWN_LABEL:
            return self._collab.windows_rule(matches, label, short)
        # lenient variant regardless of turn length; the floor uses the verification threshold
        return _continuity_rule(self._lenient(), matches, label, self.score_scale, True)

    def _lenient(self):
        return _Lenient(self)

    def describe_windows(self, matches: list, label: str | None, short: bool, rule: str) -> str:
        return describe_windows(self._lenient(), matches, label or "-", True, rule)


class _Lenient:
    """View of a TrackPolicy with the attributes `_continuity_rule` reads."""

    short_foreign_run = 2

    def __init__(self, p: TrackPolicy):
        self.threshold = p.verify_threshold if p.mode == "verified" else p.threshold
        self.window_buffer, self.window_drop = p._collab.window_buffer, p._collab.window_drop
        self.window_bad_run, self.score_scale = p._collab.window_bad_run, p.score_scale
