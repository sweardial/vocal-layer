"""Cosine matcher: identify an embedding/segment against enrolled signatures."""

from dataclasses import dataclass, field

import numpy as np

from voiceroom.embedders import Embedder
from voiceroom.enroll import Signature


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
        threshold: float,
        embedder: Embedder | None = None,
        min_margin: float = 0.0,
    ):
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
        order = np.argsort(sc)[::-1]
        best = float(sc[order[0]])
        margin = best - float(sc[order[1]]) if len(sc) > 1 else best
        scores = {self.names[i]: float(sc[i]) for i in order}
        ok = best >= self.threshold and margin >= self.min_margin
        top = self.names[order[0]]
        return Match(top if ok else None, best, margin, top, scores)
