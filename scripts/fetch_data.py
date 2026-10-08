"""Download LibriSpeech into data/ (md5-verified, resumable, skip-if-present).

uv run python scripts/fetch_data.py [--mini]

dev-clean (SLR12, 337 MB, 40 speakers) by default; --mini fetches dev-clean-2 (SLR31).
Extracts to data/LibriSpeech/<subset>/.
"""

import argparse
import hashlib
import shutil
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DOWNLOAD_DIR = DATA_DIR / "downloads"

MIRRORS = [
    "https://openslr.trmal.net/resources",
    "https://openslr.elda.org/resources",
    "https://us.openslr.org/resources",
]
# subset -> SLR resource number
SUBSETS = {"dev-clean": 12, "dev-clean-2": 31}
# Pinned md5 where verified; otherwise md5sum.txt from the mirrors is used.
PINNED_MD5 = {"dev-clean": "42e2234ba48799c1f50f24a7926300a1"}
COMPLETE = ".complete"


def md5_of(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_md5(slr: int, name: str) -> str:
    for mirror in MIRRORS:
        try:
            with urllib.request.urlopen(f"{mirror}/{slr}/md5sum.txt", timeout=30) as r:  # noqa: S310
                for line in r.read().decode().splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1].lstrip("*") == name:
                        return parts[0]
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"warn    md5sum.txt from {mirror}: {e}")
    raise SystemExit(f"could not obtain md5 for {name} from any mirror")


def download(slr: int, name: str, dest: Path) -> None:
    """Resume into dest.part, trying each mirror in turn; returns once the part is complete."""
    part = dest.with_name(dest.name + ".part")
    for mirror in MIRRORS:
        url = f"{mirror}/{slr}/{name}"
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
                if have and r.status != 206:  # server ignored Range: restart
                    have = 0
                total = have + r.length if r.length is not None else None
                print(f"fetch   {url} (from byte {have})")
                with part.open("ab" if have else "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
            if total is None or part.stat().st_size >= total:
                return
            print(f"warn    {url}: connection closed early, trying next mirror (resumes)")
        except urllib.error.HTTPError as e:
            if e.code == 416:  # already complete; md5 check decides
                return
            print(f"warn    {url}: {e}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"warn    {url}: {e}")
    raise SystemExit(f"download incomplete from all mirrors: {name} (rerun to resume)")


def extract(archive: Path, subset: str) -> None:
    """Extract to a temp dir, then rename into place (no half-extracted subset)."""
    root = DATA_DIR / "LibriSpeech"
    root.mkdir(parents=True, exist_ok=True)
    tmp = DATA_DIR / f".extract-{subset}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(tmp, filter="data")  # rejects absolute/escaping paths
    src = tmp / "LibriSpeech" / subset
    if not src.is_dir():
        raise SystemExit(f"archive did not contain LibriSpeech/{subset}")
    (src / COMPLETE).touch()
    target = root / subset
    if target.exists():
        shutil.rmtree(target)
    src.rename(target)
    shutil.rmtree(tmp)


def fetch(subset: str) -> None:
    target = DATA_DIR / "LibriSpeech" / subset
    if (target / COMPLETE).exists():
        print(f"ok      {subset} (already present)")
        return
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    slr, name = SUBSETS[subset], f"{subset}.tar.gz"
    dest = DOWNLOAD_DIR / name
    digest = PINNED_MD5.get(subset) or expected_md5(slr, name)
    if not (dest.exists() and md5_of(dest) == digest):
        dest.unlink(missing_ok=True)
        download(slr, name, dest)
        part = dest.with_name(name + ".part")
        got = md5_of(part)
        if got != digest:
            part.unlink()
            raise SystemExit(f"md5 mismatch for {name}: expected {digest}, got {got}")
        part.replace(dest)
    print(f"extract {name}")
    extract(dest, subset)
    print(f"ok      {subset}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mini", action="store_true", help="fetch dev-clean-2 instead of dev-clean")
    args = ap.parse_args()
    fetch("dev-clean-2" if args.mini else "dev-clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
