"""Build trials from synthetic conversations and score them against enrolled signatures."""

import time
from dataclasses import dataclass, field

import numpy as np

from voiceroom.audio import SAMPLE_RATE
from voiceroom.embedders import Embedder
from voiceroom.enroll import Signature, enroll
from voiceroom.eval.conversations import make_conversation, split_utterances
from voiceroom.eval.corpus import Corpus


@dataclass
class Trials:
    scores: np.ndarray  # cosine score of segment vs claimed speaker's signature
    labels: np.ndarray  # bool: segment speaker == claimed speaker (target)
    durations: np.ndarray  # segment seconds
    latencies: list[float] = field(default_factory=list)  # per segment embed seconds
    segment_durations: list[float] = field(default_factory=list)  # one per segment


def enroll_speakers(
    corpus: Corpus, speakers: list[str], embedder: Embedder, n_enroll: int, split_seed: int
) -> dict[str, Signature]:
    enroll_utts, _ = split_utterances(corpus, speakers, n_enroll, split_seed)
    sigs = {}
    for spk in speakers:
        audio = np.concatenate([u.load() for u in enroll_utts[spk]]).astype(np.float32)
        sigs[spk] = enroll(spk, audio, embedder)
    return sigs


def build_trials(
    corpus: Corpus,
    embedder: Embedder,
    *,
    n_conversations: int = 4,
    n_speakers: int = 3,
    n_turns: int = 8,
    n_enroll: int = 2,
    snr_db: float | None = None,
    seed: int = 0,
    cut_lengths: tuple[float, ...] = (0.5, 1.0, 1.5),
) -> Trials:
    """Each segment is embedded once (timed) and scored against every enrolled signature.

    Each segment is also cut at `cut_lengths` (seeded random offset, only if shorter than the
    segment) so short-utterance buckets are populated even when utterances are long.
    A (segment, signature) pair is a target trial when the signature's owner spoke the
    segment, otherwise non-target. Speaker sets rotate across conversations.
    """
    all_spk = corpus.speakers()
    n_speakers = min(n_speakers, len(all_spk))
    scores, labels, durs, lat, seg_durs = [], [], [], [], []
    rng = np.random.default_rng([seed, 7])
    embedder.embed(np.zeros(SAMPLE_RATE, np.float32))  # warm-up, excluded from latency
    sig_cache: dict[tuple[str, ...], dict[str, Signature]] = {}
    for c in range(n_conversations):
        start = (c * n_speakers) % len(all_spk)
        spks = [all_spk[(start + i) % len(all_spk)] for i in range(n_speakers)]
        key = tuple(spks)
        if key not in sig_cache:
            sig_cache[key] = enroll_speakers(corpus, spks, embedder, n_enroll, seed)
        sigs = sig_cache[key]
        conv = make_conversation(
            corpus, spks, n_turns, snr_db=snr_db, seed=seed * 1000 + c, n_enroll=n_enroll,
            split_seed=seed,
        )  # fmt: skip
        for st, en, spk in conv.segments:
            seg = conv.audio[round(st * SAMPLE_RATE) : round(en * SAMPLE_RATE)]
            clips = [seg]
            for cl in cut_lengths:
                n = round(cl * SAMPLE_RATE)
                if n < len(seg):
                    o = int(rng.integers(len(seg) - n + 1))
                    clips.append(seg[o : o + n])
            for clip in clips:
                t0 = time.perf_counter()
                emb = embedder.embed(clip)
                dt = time.perf_counter() - t0
                d = len(clip) / SAMPLE_RATE
                lat.append(dt)
                seg_durs.append(d)
                for name, sig in sigs.items():
                    scores.append(float(emb @ sig.centroid))
                    labels.append(name == spk)
                    durs.append(d)
    return Trials(np.array(scores), np.array(labels, bool), np.array(durs), lat, seg_durs)
