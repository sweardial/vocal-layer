"""Smoke: identification in a killable child process; a hung matcher times out, the child is
killed, the run ends, and the interpreter exits promptly (no stuck thread)."""

import asyncio
import time

import numpy as np

from voiceroom import synth
from voiceroom.adapters import MockProvider
from voiceroom.audio import iter_frames
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.matcher import Matcher
from voiceroom.pipeline import Pipeline
from voiceroom.policy import IsolationPolicy, Verdict
from voiceroom.segmenter import Segmenter
from voiceroom.vad import FRAME_SIZE, EnergyVAD


def build() -> Matcher:  # module-level: the spawned child re-imports this script
    emb = get_embedder("toy")
    sigs = [enroll("a", synth.utterance(1, 24.0, seed=101), emb)]
    return Matcher(sigs, threshold=0.5, embedder=emb)


class HangingMatcher(Matcher):
    def identify(self, audio16k):
        time.sleep(3600)


def build_hung() -> Matcher:
    m = build()
    m.__class__ = HangingMatcher
    return m


def make(factory, **kw) -> Pipeline:
    return Pipeline(
        EnergyVAD(), Segmenter(), build(), IsolationPolicy("a", 0.5), MockProvider(),
        matcher_factory=factory, **kw,
    )  # fmt: skip


def main() -> None:
    gap = np.zeros(16000, np.float32)
    audio = np.concatenate(
        [synth.utterance(1, 2.5, seed=7), gap, synth.utterance(1, 2.5, seed=8), gap]
    )
    ok = make(build)
    asyncio.run(ok.run(iter_frames(audio, FRAME_SIZE)))
    assert len(ok.records) == 2 and all(r.verdict is Verdict.ACCEPT for r in ok.records), ok.records
    assert ok._ident._proc is None  # child stopped at the end of the run

    hung = make(build_hung, identify_timeout_s=0.5)
    t0 = time.monotonic()
    asyncio.run(hung.run(iter_frames(audio, FRAME_SIZE)))
    assert time.monotonic() - t0 < 30
    assert len(hung.records) == 2 and all(r.error for r in hung.records), hung.records
    assert "identify_timeout" in {r.error for r in hung.records}
    assert not hung.provider.turns and hung._ident._proc is None
    print("identify isolation ok")


if __name__ == "__main__":
    main()
