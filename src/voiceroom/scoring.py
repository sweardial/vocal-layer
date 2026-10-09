"""Score functions over enrollment signatures; all embeddings are unit-norm."""

import numpy as np


def centroid_score(x: np.ndarray, centroid: np.ndarray) -> float:
    return float(x @ centroid)


def topk_chunk_score(x: np.ndarray, chunks: np.ndarray, k: int = 3) -> float:
    """Mean of the k highest cosines to the enrollment chunk embeddings (multi-centroid)."""
    sc = np.sort(chunks @ x)[::-1]
    return float(sc[:k].mean())


def kmeans_centroids(chunks: np.ndarray, k: int = 3, iters: int = 10) -> np.ndarray:
    """Deterministic spherical k-means (farthest-point init); fewer than k chunks -> chunks."""
    if len(chunks) <= k:
        return chunks
    cent = [chunks.mean(0)]
    for _ in range(k - 1):
        d = np.max(chunks @ np.stack(cent).T, axis=1)
        cent.append(chunks[int(np.argmin(d))])
    cent = np.stack(cent)
    for _ in range(iters):
        a = np.argmax(chunks @ cent.T, axis=1)
        for j in range(len(cent)):
            if (a == j).any():
                v = chunks[a == j].mean(0)
                cent[j] = v / (np.linalg.norm(v) + 1e-12)
    return cent


MIN_NORM_STD = 1e-3  # below this the cohort score spread is degenerate: z-scores are meaningless


def norm_stats(cohort: np.ndarray, v: np.ndarray, top_n: int) -> tuple[float, float]:
    """Mean and std of the top-n cohort scores. Raises ValueError if the spread is degenerate
    (duplicate/identical cohort rows), so callers fail closed instead of dividing by ~0."""
    sc = np.sort(cohort @ v)[::-1][:top_n]
    mean, std = float(sc.mean()), float(sc.std())
    if not (np.isfinite(mean) and np.isfinite(std)) or std < MIN_NORM_STD:
        raise ValueError("degenerate cohort score distribution")
    return mean, std


def as_norm(
    score: float, x: np.ndarray, centroid: np.ndarray, cohort: np.ndarray, top_n: int = 100
) -> float:
    """Adaptive symmetric score norm against `cohort` (n, dim) of impostor embeddings."""
    me, se = norm_stats(cohort, centroid, top_n)
    mt, st = norm_stats(cohort, x, top_n)
    return 0.5 * ((score - me) / se + (score - mt) / st)
