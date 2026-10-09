"""Cosine matcher: identify an embedding/segment against enrolled signatures."""

from dataclasses import dataclass, field

import numpy as np

from voiceroom.config import ASNORM_TOP_N, MIN_MARGIN, asnorm_threshold_for, threshold_for
from voiceroom.embedders import Embedder
from voiceroom.enroll import Signature
from voiceroom.scoring import norm_stats


@dataclass(frozen=True)
class Match:
    name: str | None  # None = unknown (best score below threshold)
    score: float  # best cosine score
    margin: float  # best minus runner-up (best if single candidate)
    best: str | None = None  # best candidate even when rejected
    scores: dict[str, float] = field(default_factory=dict)


class Matcher:
    def __init__(
        self,
        signatures: list[Signature],
        threshold: float | None = None,
        embedder: Embedder | None = None,
        min_margin: float = MIN_MARGIN,
        cohort: np.ndarray | None = None,
    ):
        """`cohort` (n, dim) of impostor embeddings switches on AS-norm scoring."""
        if not signatures:
            raise ValueError("Matcher needs at least one signature")
        if len({s.name for s in signatures}) != len(signatures):
            raise ValueError("duplicate signature names")
        if len({s.embedder for s in signatures}) != 1:
            raise ValueError("signatures come from different embedders")
        if embedder is not None and embedder.name != signatures[0].embedder:
            raise ValueError("embedder does not match signatures")
        self.names = [s.name for s in signatures]
        self._cent = np.stack([s.centroid for s in signatures])
        self.cohort = None
        if cohort is not None:
            c = np.asarray(cohort, np.float32)
            if c.ndim != 2 or c.shape[1] != self._cent.shape[1] or len(c) < 2:
                raise ValueError("cohort must be (n >= 2, dim) matching the signatures")
            norms = np.linalg.norm(c, axis=1, keepdims=True)
            if not np.isfinite(c).all() or (norms < 1e-6).any():
                raise ValueError("cohort has non-finite or zero vectors")
            self.cohort = c / norms
            self._enroll_stats = [norm_stats(self.cohort, v, ASNORM_TOP_N) for v in self._cent]
        if threshold is None:
            name = signatures[0].embedder
            threshold = threshold_for(name) if self.cohort is None else asnorm_threshold_for(name)
        self.threshold = threshold
        self.min_margin = min_margin
        self.embedder = embedder

    def identify(self, audio16k: np.ndarray) -> Match:
        """Identify a 16 kHz mono segment (needs the Matcher's embedder)."""
        if self.embedder is None:
            raise ValueError("Matcher has no embedder; use identify_embedding")
        return self.identify_embedding(self.embedder.embed(np.asarray(audio16k, np.float32)))

    def identify_embedding(self, embedding: np.ndarray) -> Match:
        x = np.asarray(embedding, np.float32)
        if x.shape != (self._cent.shape[1],):
            raise ValueError(f"expected embedding of shape {(self._cent.shape[1],)}, got {x.shape}")
        n = np.linalg.norm(x)
        x = x / n if n > 0 else x
        sc = self._cent @ x
        if self.cohort is not None:
            try:
                mt, st = norm_stats(self.cohort, x, ASNORM_TOP_N)
            except ValueError:  # degenerate normalization: fail closed (reject)
                names = {n: float("-inf") for n in self.names}
                return Match(None, float("-inf"), 0.0, self.names[0], names)
            sc = np.array(
                [
                    0.5 * ((v - me) / se + (v - mt) / st)
                    for v, (me, se) in zip(sc, self._enroll_stats, strict=True)
                ]
            )
        order = np.argsort(sc)[::-1]
        best = float(sc[order[0]])
        margin = best - float(sc[order[1]]) if len(sc) > 1 else best
        scores = {self.names[i]: float(sc[i]) for i in order}
        ok = best >= self.threshold and margin >= self.min_margin
        top = self.names[order[0]]
        return Match(top if ok else None, best, margin, top, scores)
