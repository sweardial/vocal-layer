"""Encrypted file-based signature store (D-006): one Fernet-encrypted file per signature."""

import base64
import hashlib
import hmac
import json
import os
import stat
import tempfile
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


def _read_keyfile(path: Path) -> bytes:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise StoreKeyError(f"keyfile {path} is not a regular file")
    if st.st_uid != os.geteuid():
        raise StoreKeyError(f"keyfile {path} is not owned by the current user")
    if st.st_mode & 0o077:
        raise StoreKeyError(
            f"keyfile {path} is accessible to group/others (mode {stat.S_IMODE(st.st_mode):04o}); "
            f"run `chmod 600 {path}` (and consider rotating the key if it may have been exposed)"
        )
    key = path.read_bytes().strip()
    try:
        Fernet(key)
    except (ValueError, TypeError) as e:
        raise StoreKeyError(
            f"keyfile {path} is empty or malformed; restore it from backup, or delete it to "
            "generate a new key (existing signatures will then be unreadable)"
        ) from e
    return key


def load_key(keyfile: Path | None = None) -> bytes:
    """Key from $VOICEROOM_KEY, else from the keyfile (created with mode 0600 if missing)."""
    env = os.environ.get(KEY_ENV)
    if env:
        return env.encode()
    path = Path(keyfile) if keyfile else DEFAULT_KEYFILE
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_symlink() and not path.exists():
        # Write fully to a private temp file, then link into place: link() never replaces a
        # competing initializer's key, and the keyfile is never visible half-written.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".key-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(tmp)
    return _read_keyfile(path)


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
        self._key = key.encode() if isinstance(key, str) else bytes(key)
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return f"SignatureStore(root={str(self.root)!r})"

    def _path(self, name: str) -> Path:
        # Keyed hash so filenames don't reveal who is enrolled to anyone without the key.
        digest = hmac.new(self._key, name.encode(), hashlib.sha256).hexdigest()
        return self.root / (digest + _SUFFIX)

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
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tmp-", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(self._fernet.encrypt(json.dumps(rec).encode()))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _record(self, name: str) -> dict:
        path = self._path(name)
        if not path.exists():
            # Filenames are keyed, so a wrong key looks like "not found": surface it clearly.
            for other in self.root.glob("*" + _SUFFIX):
                self._read(other)
                break
            raise KeyError(name)
        rec = self._read(path)
        if rec["name"] != name:  # file swapped in from another entry
            raise StoreError(f"{path.name} does not belong to the requested name")
        return rec

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
        """Names of readable signatures; undecryptable files are skipped unless none decrypt."""
        names, failed = [], None
        for p in self.root.glob("*" + _SUFFIX):
            try:
                names.append(self._read(p)["name"])
            except StoreKeyError as e:
                failed = e
        if failed and not names:
            raise failed
        return sorted(names)

    def delete(self, name: str) -> bool:
        """Remove the signature file. Returns False if it did not exist."""
        try:
            self._path(name).unlink()
        except FileNotFoundError:
            return False
        return True
