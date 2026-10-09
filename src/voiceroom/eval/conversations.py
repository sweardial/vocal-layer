"""Synthetic multi-speaker conversations with ground-truth segments."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from ..audio import SAMPLE_RATE
from .corpus import Corpus, Utterance

Segment = tuple[float, float, str]  # (start_s, end_s, speaker)


@dataclass
class Conversation:
    audio: np.ndarray  # 16 kHz mono float32
    segments: list[Segment]  # sorted, non-overlapping
    utterance_ids: list[str]  # source utterance of each segment
    enrollment_ids: dict[str, list[str]]  # speaker -> utterances held out for enrollment


def split_utterances(
    corpus: Corpus, speakers: list[str], n_enroll: int = 2, seed: int = 0
) -> tuple[dict[str, list[Utterance]], dict[str, list[Utterance]]]:
    """Per speaker, a seeded disjoint (enrollment, test) split; test is never empty."""
    enroll, test = {}, {}
    for spk in speakers:
        utts = sorted(corpus.utterances(spk), key=lambda u: u.id)
        if len(utts) < n_enroll + 1:
            raise ValueError(f"speaker {spk} has {len(utts)} utterances, need > {n_enroll}")
        order = np.random.default_rng([seed, _stable_int(spk)]).permutation(len(utts))
        enroll[spk] = [utts[i] for i in order[:n_enroll]]
        test[spk] = [utts[i] for i in order[n_enroll:]]
    return enroll, test


def _stable_int(s: str) -> int:
    return int.from_bytes(s.encode()[:8].ljust(8, b"\0"), "big") ^ sum(map(ord, s))


def make_conversation(
    corpus: Corpus,
    speakers: list[str],
    n_turns: int,
    pause_range: tuple[float, float] = (0.2, 1.0),
    snr_db: float | None = None,
    seed: int = 0,
    n_enroll: int = 2,
    split_seed: int = 0,
) -> Conversation:
    """Alternating turns (never the same speaker twice in a row) from test utterances only.

    The enrollment/test split depends on `split_seed`, not `seed`, so one enrollment set
    can serve many conversations.
    """
    if not 2 <= len(speakers) <= 4:
        raise ValueError("need 2-4 speakers")
    enroll, test = split_utterances(corpus, speakers, n_enroll, split_seed)
    rng = np.random.default_rng(seed)
    pieces, segments, ids = [], [], []
    pos = 0  # in samples
    prev = None
    for turn in range(n_turns):
        spk = speakers[int(rng.choice([i for i, s in enumerate(speakers) if s != prev]))]
        utt = test[spk][int(rng.integers(len(test[spk])))]
        if turn:
            pause = int(round(float(rng.uniform(*pause_range)) * SAMPLE_RATE))
            pieces.append(np.zeros(pause, dtype=np.float32))
            pos += pause
        x = utt.load().astype(np.float32)
        pieces.append(x)
        segments.append((pos / SAMPLE_RATE, (pos + len(x)) / SAMPLE_RATE, spk))
        ids.append(utt.id)
        pos += len(x)
        prev = spk
    sig = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    if snr_db is not None and len(sig):
        speech = np.concatenate([p for p in pieces if p.any()])
        power = float(np.mean(speech**2))  # speech only, pauses excluded
        noise = rng.standard_normal(len(sig)) * np.sqrt(power / 10 ** (snr_db / 10))
        sig = (sig + noise).astype(np.float32)
    return Conversation(sig, segments, ids, {s: [u.id for u in v] for s, v in enroll.items()})


def save_conversation(conv: Conversation, path: str | Path) -> None:
    """Write `<path>.wav` (float32 WAV) and `<path>.json`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(Path(f"{path}.wav"), conv.audio, SAMPLE_RATE, subtype="FLOAT")
    meta = {
        "segments": [list(s) for s in conv.segments],
        "utterance_ids": conv.utterance_ids,
        "enrollment_ids": conv.enrollment_ids,
    }
    Path(f"{path}.json").write_text(json.dumps(meta, indent=1))


def load_conversation(path: str | Path) -> Conversation:
    path = Path(path)
    x, sr = sf.read(Path(f"{path}.wav"), dtype="float32")
    if sr != SAMPLE_RATE:
        raise ValueError(f"expected {SAMPLE_RATE} Hz, got {sr}")
    meta = json.loads(Path(f"{path}.json").read_text())
    segs = [(float(a), float(b), s) for a, b, s in meta["segments"]]
    return Conversation(x, segs, meta["utterance_ids"], meta["enrollment_ids"])
