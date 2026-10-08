"""Embedder protocol: 16 kHz mono float32 audio in, L2-normalised speaker vector out."""

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, audio16k: np.ndarray) -> np.ndarray:
        """Return a unit-norm float32 vector of shape (dim,)."""
        ...


def embed_batch(embedder: Embedder, clips: list[np.ndarray]) -> np.ndarray:
    """Use the embedder's own `embed_batch` if present, else loop. Returns (n, dim)."""
    fn = getattr(embedder, "embed_batch", None)
    if fn is not None:
        return fn(clips)
    if not clips:
        return np.zeros((0, embedder.dim), np.float32)
    return np.stack([embedder.embed(c) for c in clips])


def l2_normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v
