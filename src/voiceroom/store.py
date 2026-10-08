"""Encrypted file-based signature store (D-006): one Fernet-encrypted file per signature."""

import base64
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet, InvalidToken

from voiceroom.enroll import Signature

KEY_ENV = "VOICEROOM_KEY"
DEFAULT_KEYFILE = Path.home() / ".voiceroom" / "key"
_SUFFIX = ".sig"


class StoreError(Exception):
    pass


class StoreKeyError(StoreError):
    """The key is malformed or does not decrypt the stored data."""


def load_key(keyfile: Path | None = None) -> bytes:
    """Key from $VOICEROOM_KEY, else from the keyfile (created with mode 0600 if missing)."""
    env = os.environ.get(KEY_ENV)
    if env:
        return env.encode()
    path = Path(keyfile) if keyfile else DEFAULT_KEYFILE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes().strip()
    with os.fdopen(fd, "wb") as f:
        f.write(Fernet.generate_key())
    return path.read_bytes().strip()


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a, np.float32).tobytes()).decode()


def _arr(s: str, dim: int | None = None) -> np.ndarray:
    a = np.frombuffer(base64.b64decode(s), np.float32).copy()
    return a if dim is None else a.reshape(-1, dim)


class SignatureStore:
    def __init__(self, root: Path | str, key: bytes | str):
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as e:
            raise StoreKeyError("invalid key: expected a 32-byte urlsafe-base64 Fernet key") from e
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return f"SignatureStore(root={str(self.root)!r})"

    def _path(self, name: str) -> Path:
        # Hashed so filenames don't reveal who is enrolled.
        return self.root / (hashlib.sha256(name.encode()).hexdigest() + _SUFFIX)

    def _read(self, path: Path) -> dict:
        try:
            return json.loads(self._fernet.decrypt(path.read_bytes()))
        except InvalidToken as e:
            raise StoreKeyError(f"cannot decrypt {path.name}: wrong key or corrupted file") from e

    def save(self, sig: Signature, consent: dict | None = None) -> None:
        """Write or overwrite a signature; `consent` is free-form metadata."""
        dim = int(sig.centroid.shape[-1])
        rec = {
            "name": sig.name,
            "embedder": sig.embedder,
            "dim": dim,
            "centroid": _b64(sig.centroid),
            "chunks": _b64(sig.chunks),
            "speech_seconds": sig.speech_seconds,
            "consent": consent or {},
            "saved_at": time.time(),
        }
        path = self._path(sig.name)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(self._fernet.encrypt(json.dumps(rec).encode()))
        os.replace(tmp, path)

    def _record(self, name: str) -> dict:
        path = self._path(name)
        if not path.exists():
            raise KeyError(name)
        return self._read(path)

    def load(self, name: str) -> Signature:
        rec = self._record(name)
        return Signature(
            rec["name"],
            rec["embedder"],
            _arr(rec["centroid"]),
            _arr(rec["chunks"], rec["dim"]),
            rec["speech_seconds"],
        )

    def metadata(self, name: str) -> dict:
        """Consent info and timestamp for a stored signature (no embeddings)."""
        rec = self._record(name)
        return {"consent": rec["consent"], "saved_at": rec["saved_at"], "embedder": rec["embedder"]}

    def list(self) -> list[str]:
        return sorted(self._read(p)["name"] for p in self.root.glob("*" + _SUFFIX))

    def delete(self, name: str) -> bool:
        """Remove the signature file. Returns False if it did not exist."""
        try:
            self._path(name).unlink()
        except FileNotFoundError:
            return False
        return True
