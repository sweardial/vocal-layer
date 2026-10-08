"""Download pinned model files into models/ (idempotent, sha256-verified).

uv run python scripts/fetch_models.py [--only wespeaker|silero|ecapa ...]
"""

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"

_WESPEAKER_REV = "f0c48c298fd835726c27956a5d617bad7115627e"
_SILERO_TAG = "v5.1.2"
_ECAPA_REV = "0f99f2d0ebe89ac095bcc5903c4dd8f72b367286"

# key -> (filename, url, sha256)
MODELS = {
    "wespeaker": (
        "voxceleb_resnet34_LM.onnx",
        "https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/"
        f"{_WESPEAKER_REV}/voxceleb_resnet34_LM.onnx",
        "7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068",
    ),
    "silero": (
        "silero_vad.onnx",
        f"https://raw.githubusercontent.com/snakers4/silero-vad/{_SILERO_TAG}/"
        "src/silero_vad/data/silero_vad.onnx",
        "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f",
    ),
    # Only the embedding weights; the architecture is built in code (embedders/ecapa.py).
    "ecapa": (
        "ecapa_voxceleb_embedding_model.ckpt",
        "https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb/resolve/"
        f"{_ECAPA_REV}/embedding_model.ckpt",
        "0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2",
    ),
}

NOTICE = f"""\
Model files in this directory are downloaded by scripts/fetch_models.py (not committed).

voxceleb_resnet34_LM.onnx
  WeSpeaker ResNet34-LM speaker embedding model, trained on VoxCeleb.
  Source: https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM (rev {_WESPEAKER_REV})
  Project: https://github.com/wenet-e2e/wespeaker
  License: CC-BY-4.0 (https://creativecommons.org/licenses/by/4.0/). Attribution required:
  "WeSpeaker ResNet34-LM by the WeSpeaker authors, licensed under CC BY 4.0".
  The VoxCeleb training data has its own terms; check them before commercial use.

silero_vad.onnx
  Silero VAD, https://github.com/snakers4/silero-vad (tag {_SILERO_TAG}), MIT License,
  Copyright (c) Silero Team.

ecapa_voxceleb_embedding_model.ckpt
  SpeechBrain ECAPA-TDNN speaker embedding weights (embedding_model.ckpt), trained on VoxCeleb.
  Source: https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb (rev {_ECAPA_REV})
  License: Apache-2.0. The VoxCeleb training data has its own terms.
"""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(key: str) -> None:
    name, url, digest = MODELS[key]
    dest = MODELS_DIR / name
    if dest.exists() and sha256_of(dest) == digest:
        print(f"ok      {name} (already present)")
        return
    tmp = dest.with_name(dest.name + ".part")
    print(f"fetch   {name}")
    with urllib.request.urlopen(url, timeout=60) as r, tmp.open("wb") as f:  # noqa: S310
        while chunk := r.read(1 << 20):
            f.write(chunk)
    got = sha256_of(tmp)
    if got != digest:
        tmp.unlink()
        raise SystemExit(f"sha256 mismatch for {name}: expected {digest}, got {got}")
    tmp.replace(dest)
    print(f"ok      {name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="+", choices=sorted(MODELS), help="fetch just these models")
    args = ap.parse_args()
    MODELS_DIR.mkdir(exist_ok=True)
    for key in args.only or MODELS:
        fetch(key)
    (MODELS_DIR / "README").write_text(NOTICE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
