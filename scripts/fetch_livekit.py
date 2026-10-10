"""Download a pinned `livekit-server` release into data/bin/ (idempotent, sha256-verified).

uv run python scripts/fetch_livekit.py

Upstream publishes no macOS binary; on macOS install it with `brew install livekit` (the
adapter finds `livekit-server` on PATH) or point LIVEKIT_SERVER_BIN at it.
"""

import hashlib
import io
import platform
import sys
import tarfile
import urllib.request
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parents[1] / "data" / "bin"
VERSION = "1.13.9"
URL = f"https://github.com/livekit/livekit/releases/download/v{VERSION}/"

# machine -> (archive, sha256 of the archive; from the release's checksums.txt)
ARCHIVES = {
    "x86_64": (
        f"livekit_{VERSION}_linux_amd64.tar.gz",
        "0b7fa208b662d09cfdeae8c06cf4c481aead0556086558b48250501e2e2d6e20",
    ),
    "aarch64": (
        f"livekit_{VERSION}_linux_arm64.tar.gz",
        "c5e4e5a6d6cf0a449651aa3178532ecc7a2bf2502538396ebe6e51ff13424056",
    ),
}


def main() -> int:
    if sys.platform != "linux":
        print("No upstream livekit-server binary for this OS; run `brew install livekit`.")
        return 1
    machine = platform.machine().lower()
    if machine == "amd64":
        machine = "x86_64"
    if machine == "arm64":
        machine = "aarch64"
    if machine not in ARCHIVES:
        raise SystemExit(f"unsupported architecture: {machine}")
    name, digest = ARCHIVES[machine]
    dest = BIN_DIR / "livekit-server"
    stamp = BIN_DIR / f"livekit-server.{digest[:12]}"
    if dest.exists() and stamp.exists():
        print(f"ok      livekit-server {VERSION} (already present)")
        return 0
    print(f"fetch   {name}")
    with urllib.request.urlopen(URL + name, timeout=120) as r:  # noqa: S310
        data = r.read()
    got = hashlib.sha256(data).hexdigest()
    if got != digest:
        raise SystemExit(f"sha256 mismatch for {name}: expected {digest}, got {got}")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        member = tar.getmember("livekit-server")
        if not member.isfile():
            raise SystemExit("livekit-server is not a regular file in the archive")
        src = tar.extractfile(member)
        assert src is not None
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(src.read())
    tmp.chmod(0o755)
    tmp.replace(dest)
    for old in BIN_DIR.glob("livekit-server.*"):
        old.unlink()
    stamp.write_text(VERSION + "\n")
    print(f"ok      livekit-server {VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
