"""Evaluation corpora: speakers -> utterances with duration."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
import soundfile as sf

from .. import audio, synth


@dataclass(frozen=True)
class Utterance:
    id: str
    speaker: str
    duration: float  # seconds
    _load: Callable[[], np.ndarray] = field(compare=False, repr=False)

    def load(self) -> np.ndarray:
        """16 kHz mono float32."""
        return self._load()


class Corpus(Protocol):
    def speakers(self) -> list[str]: ...

    def utterances(self, speaker: str) -> list[Utterance]: ...


class LibriSpeechCorpus:
    """Index of `<root>/<subset>/<speaker>/<chapter>/*.flac`; root is e.g. data/LibriSpeech."""

    def __init__(self, root: str | Path, subset: str | None = None):
        root = Path(root)
        if subset is None:
            subsets = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
            if not subsets:
                raise FileNotFoundError(f"no LibriSpeech subset under {root}")
            subset = subsets[0]
        base = root / subset
        if not base.is_dir():
            raise FileNotFoundError(base)
        self._by_speaker: dict[str, list[Utterance]] = {}
        for spk_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            utts = []
            for f in sorted(spk_dir.glob("*/*.flac")):
                utts.append(Utterance(f.stem, spk_dir.name, sf.info(f).duration, _file_loader(f)))
            if utts:
                self._by_speaker[spk_dir.name] = utts

    def speakers(self) -> list[str]:
        return list(self._by_speaker)

    def utterances(self, speaker: str) -> list[Utterance]:
        return list(self._by_speaker[speaker])


def _file_loader(path: Path) -> Callable[[], np.ndarray]:
    return lambda: audio.load_audio(path)


class SynthCorpus:
    """Deterministic synthetic corpus: speaker i uses a profile seed derived from (seed, i)."""

    def __init__(
        self,
        n_speakers: int,
        seed: int = 0,
        utts_per_speaker: int = 6,
        duration_range: tuple[float, float] = (2.0, 6.0),
    ):
        rng = np.random.default_rng(seed)
        profile_seeds = [int(x) for x in rng.integers(1 << 31, size=n_speakers)]
        self._by_speaker: dict[str, list[Utterance]] = {}
        for i in range(n_speakers):
            spk = f"synth{i:03d}"
            utts = []
            for j in range(utts_per_speaker):
                dur = round(float(rng.uniform(*duration_range)), 2)
                utt_seed = int(rng.integers(1 << 31))
                utts.append(
                    Utterance(
                        f"{spk}-{j:03d}", spk, dur, _synth_loader(profile_seeds[i], dur, utt_seed)
                    )
                )
            self._by_speaker[spk] = utts

    def speakers(self) -> list[str]:
        return list(self._by_speaker)

    def utterances(self, speaker: str) -> list[Utterance]:
        return list(self._by_speaker[speaker])


def _synth_loader(profile_seed: int, dur: float, utt_seed: int) -> Callable[[], np.ndarray]:
    return lambda: synth.utterance(profile_seed, dur, seed=utt_seed)
