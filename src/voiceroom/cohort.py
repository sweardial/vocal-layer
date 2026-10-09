"""AS-norm cohort: impostor embeddings plus the calibration that only holds for them (D-013).

The file holds embeddings (no audio) plus the AS-norm threshold / score scale measured against
exactly those embeddings (so the three cannot drift apart); Fernet-encrypted with the
signature-store key. Default location: `data/`.
"""

import io
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet, InvalidToken

from voiceroom.config import ASNORM_TOP_N
from voiceroom.scoring import norm_stats

DEFAULT_DIR = Path("data")
FORMAT = 1


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
        if self.score_scale <= 0 or self.top_n < 1:
            raise ValueError("cohort score_scale and top_n must be positive")

    def save(self, path: Path, key: bytes | str) -> None:
        """Fernet-encrypted (authenticated) npz, mode 0600; same key as the signature store."""
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
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet(key).encrypt(buf.getvalue()))
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path, key: bytes | str) -> "Cohort":
        try:
            raw = Fernet(key).decrypt(Path(path).read_bytes())
        except InvalidToken as e:
            raise ValueError(f"cannot decrypt {path}: wrong key or corrupted file") from e
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


def load_default(embedder: str, key: bytes | str, directory: Path = DEFAULT_DIR) -> Cohort | None:
    """The shipped cohort for `embedder`, or None when absent (AS-norm stays off)."""
    path = default_path(embedder, directory)
    if not path.exists():
        return None
    c = Cohort.load(path, key)
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
