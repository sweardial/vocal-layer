"""Speaker-verification metrics over scored trials (higher score = more likely the owner)."""

import numpy as np

BUCKETS: list[tuple[str, float, float]] = [
    ("<1 s", 0.0, 1.0),
    ("1-2 s", 1.0, 2.0),
    ("2-4 s", 2.0, 4.0),
    (">=4 s", 4.0, float("inf")),
]


def _split(scores: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    scores, labels = np.asarray(scores, float), np.asarray(labels, bool)
    return scores[labels], scores[~labels]


def far_frr(scores: np.ndarray, labels: np.ndarray, threshold: float) -> tuple[float, float]:
    """Accept when score >= threshold. FAR = non-targets accepted, FRR = targets rejected.

    A rate with no trials of its class is NaN.
    """
    tgt, non = _split(scores, labels)
    far = float((non >= threshold).mean()) if len(non) else float("nan")
    frr = float((tgt < threshold).mean()) if len(tgt) else float("nan")
    return far, frr


def eer(scores: np.ndarray, labels: np.ndarray) -> tuple[float, float]:
    """Equal error rate and its threshold. (nan, nan) if either class is empty."""
    tgt, non = _split(scores, labels)
    if not len(tgt) or not len(non):
        return float("nan"), float("nan")
    cand = np.unique(np.concatenate([tgt, non]))
    tgt_s, non_s = np.sort(tgt), np.sort(non)
    frr = np.searchsorted(tgt_s, cand, side="left") / len(tgt)  # targets < t
    far = 1.0 - np.searchsorted(non_s, cand, side="left") / len(non)  # non-targets >= t
    i = int(np.argmin(np.abs(far - frr)))
    return float((far[i] + frr[i]) / 2), float(cand[i])


def threshold_for_far(scores: np.ndarray, labels: np.ndarray, target_far: float) -> float:
    """Lowest threshold whose FAR is <= target_far (so FRR is minimal under that constraint)."""
    _, non = _split(scores, labels)
    if not len(non):
        raise ValueError("no non-target trials")
    cand = np.unique(np.asarray(scores, float))
    non_s = np.sort(non)
    far = 1.0 - np.searchsorted(non_s, cand, side="left") / len(non)
    ok = np.nonzero(far <= target_far)[0]
    if len(ok):
        return float(cand[ok[0]])
    return float(np.nextafter(cand[-1], np.inf))  # nothing passes: reject everything


def bucket_of(duration_s: float) -> str:
    for name, lo, hi in BUCKETS:
        if lo <= duration_s < hi:
            return name
    raise ValueError(duration_s)


def by_length(
    scores: np.ndarray, labels: np.ndarray, durations: np.ndarray, threshold: float
) -> dict[str, dict]:
    """Per length bucket: trial counts, EER, and FAR/FRR at `threshold`."""
    scores, labels, durations = np.asarray(scores), np.asarray(labels, bool), np.asarray(durations)
    out = {}
    for name, lo, hi in BUCKETS:
        m = (durations >= lo) & (durations < hi)
        far, frr = far_frr(scores[m], labels[m], threshold)
        e, _ = eer(scores[m], labels[m])
        out[name] = {
            "n_target": int(labels[m].sum()),
            "n_nontarget": int((~labels[m]).sum()),
            "eer": e,
            "far": far,
            "frr": frr,
        }
    return out


def latency_stats(latencies_s: list[float]) -> dict[str, float]:
    a = np.asarray(latencies_s, float)
    if not len(a):
        return {"p50_ms": float("nan"), "p95_ms": float("nan"), "mean_ms": float("nan")}
    return {
        "p50_ms": float(np.percentile(a, 50) * 1e3),
        "p95_ms": float(np.percentile(a, 95) * 1e3),
        "mean_ms": float(a.mean() * 1e3),
    }


def rtf(latencies_s: list[float], durations_s: list[float]) -> float:
    """Real-time factor: embedding time / audio duration (< 1 = faster than real time)."""
    total = float(np.sum(durations_s))
    return float(np.sum(latencies_s)) / total if total > 0 else float("nan")
