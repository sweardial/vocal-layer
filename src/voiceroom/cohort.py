"""AS-norm cohort: impostor embeddings plus the calibration that only holds for them (D-013).

The file holds embeddings (no audio) plus the AS-norm threshold / score scale measured against
exactly those embeddings (so the three cannot drift apart). Stored as a plain npz (no key):
it holds no personal data, and this keeps the file portable across machines (T-034).
Default location: `data/`.
"""

import hashlib
import hmac
import io
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from voiceroom.config import ASNORM_TOP_N, COHORT_SHA256
from voiceroom.scoring import norm_stats

DEFAULT_DIR = Path("data")
FORMAT = 1
ZIP_MAGIC = b"PK"


@dataclass(frozen=True)
class Cohort:
    embeddings: np.ndarray = field(repr=False)  # (n, dim) unit-norm impostor embeddings
    embedder: str
    threshold: float  # AS-norm accept threshold (FAR ~1 %) valid for these embeddings
    top_n: int = ASNORM_TOP_N
    score_scale: float = 1.0  # z-units per cosine unit: rescales cosine-scale policy buffers
    speakers: tuple[str, ...] = ()  # corpus speakers the windows came from (bench excludes them)

    def __post_init__(self):
        e = self.embeddings
        if e.ndim != 2 or len(e) < 2 or not np.isfinite(e).all():
            raise ValueError("cohort embeddings must be a finite (n >= 2, dim) array")
        if not (np.isfinite(self.threshold) and np.isfinite(self.score_scale)):
            raise ValueError("cohort threshold/score_scale must be finite")
        if not 0.0 <= self.threshold <= 20.0:  # plain file is unauthenticated: reject absurd values
            raise ValueError("cohort threshold out of range")
        if self.score_scale <= 0 or self.top_n < 1:
            raise ValueError("cohort score_scale and top_n must be positive")

    def save(self, path: Path) -> str:
        """Plain npz (embeddings only, no audio or speaker data), written atomically."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        np.savez(
            buf,
            format=FORMAT,
            embeddings=self.embeddings.astype(np.float32),
            embedder=self.embedder,
            threshold=self.threshold,
            top_n=self.top_n,
            score_scale=self.score_scale,
            speakers=np.array(self.speakers, dtype=str),
        )
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(buf.getvalue())
        os.replace(tmp, path)
        return file_sha256(path)

    @classmethod
    def load(cls, path: Path, sha256: str) -> "Cohort":
        """Load only if the file SHA-256 equals the trusted `sha256` (file is unauthenticated)."""
        raw = Path(path).read_bytes()
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), sha256.lower()):
            raise ValueError(f"{path} does not match the pinned digest (altered or stale cohort)")
        if not raw.startswith(ZIP_MAGIC):
            raise ValueError(f"{path} is not a plain cohort npz (old encrypted file? rebuild it)")
        with np.load(io.BytesIO(raw), allow_pickle=False) as z:
            if int(z["format"]) != FORMAT:
                raise ValueError(f"unsupported cohort format in {path}")
            return cls(
                z["embeddings"].astype(np.float32),
                str(z["embedder"]),
                float(z["threshold"]),
                int(z["top_n"]),
                float(z["score_scale"]),
                tuple(str(s) for s in z["speakers"]),
            )


def default_path(embedder: str, directory: Path = DEFAULT_DIR) -> Path:
    return Path(directory) / f"cohort-{embedder}.npz"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_default(
    embedder: str, directory: Path = DEFAULT_DIR, sha256: str | None = None
) -> Cohort | None:
    """The shipped cohort for `embedder`, or None when absent (AS-norm stays off)."""
    path = default_path(embedder, directory)
    if not path.exists():
        return None
    pin = sha256 or COHORT_SHA256.get(embedder)
    if pin is None:
        raise ValueError(f"{path} exists but no trusted digest is pinned for {embedder!r}")
    c = Cohort.load(path, pin)
    if c.embedder != embedder:
        raise ValueError(f"{path} is for embedder {c.embedder!r}, not {embedder!r}")
    return c


def estimate_score_scale(
    emb: np.ndarray, labels: list[str] | None = None, top_n: int = ASNORM_TOP_N
) -> float:
    """1 / median top-n cohort score std, leave-one-speaker-out (`labels` = speaker per row;
    default one speaker per row) so each row looks like a probe from outside the cohort."""
    labels = np.arange(len(emb)).astype(str) if labels is None else np.asarray(labels)
    stds = []
    for i in range(len(emb)):
        rest = emb[labels != labels[i]]
        stds.append(norm_stats(rest, emb[i], min(top_n, len(rest)))[1])
    return float(1.0 / np.median(stds))
