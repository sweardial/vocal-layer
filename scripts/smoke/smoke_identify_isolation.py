"""Smoke: identification in a killable child process; a hung matcher times out, the child is
killed, the run ends, and the interpreter exits promptly (no stuck thread)."""

import asyncio
import os
import signal
import threading
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


class FreezingMatcher(Matcher):
    """Answers once, then the whole child process stops (SIGSTOP) without reading again."""

    def identify(self, audio16k):
        r = super().identify(audio16k)
        threading.Timer(0.05, os.kill, (os.getpid(), signal.SIGSTOP)).start()
        return r


def build_frozen() -> Matcher:
    m = build()
    m.__class__ = FreezingMatcher
    return m


def make(factory=None, matcher=None, **kw) -> Pipeline:
    return Pipeline(
        EnergyVAD(), Segmenter(), matcher or build(), IsolationPolicy("a", 0.5), MockProvider(),
        matcher_factory=factory, **kw,
    )  # fmt: skip


def main() -> None:
    gap = np.zeros(16000, np.float32)
    audio = np.concatenate(
        [synth.utterance(1, 2.5, seed=7), gap, synth.utterance(1, 2.5, seed=8), gap]
    )
    # Default public construction (isolation="process", picklable matcher, no factory).
    ok = make()
    asyncio.run(ok.run(iter_frames(audio, FRAME_SIZE)))
    assert len(ok.records) == 2 and all(r.verdict is Verdict.ACCEPT for r in ok.records), ok.records
    assert ok._ident._proc is None  # child stopped at the end of the run

    # A matcher that never returns: default construction, hung instance passed directly.
    hm = build()
    hm.__class__ = HangingMatcher
    for pipe in (make(matcher=hm, identify_timeout_s=0.5),):
        t0 = time.monotonic()
        asyncio.run(pipe.run(iter_frames(audio, FRAME_SIZE)))
        assert time.monotonic() - t0 < 30
        assert len(pipe.records) == 2 and all(r.error for r in pipe.records), pipe.records
        assert "identify_timeout" in {r.error for r in pipe.records}
        assert not pipe.provider.turns and pipe._ident._proc is None and pipe._ident.kills >= 1

    # A child that is ready but stops reading: sending a large turn must not block the event
    # loop; the whole request has a deadline and the child is killed.
    long = np.concatenate([synth.utterance(1, 12.0, seed=9), gap])
    frozen = make(build_frozen, identify_timeout_s=1.0)
    t0 = time.monotonic()
    asyncio.run(frozen.run(iter_frames(np.concatenate([long, long]), FRAME_SIZE)))
    assert time.monotonic() - t0 < 60
    assert frozen._ident.kills >= 1 and frozen._ident._proc is None
    assert {"identify_timeout"} & {r.error for r in frozen.records}, frozen.records
    print("identify isolation ok")


if __name__ == "__main__":
    main()
