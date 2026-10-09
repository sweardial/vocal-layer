"""Smoke: FileSource -> pipeline (MockProvider) -> WavSink on a synthetic conversation."""

import asyncio
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

import numpy as np
import soundfile as sf

from voiceroom.adapters import MockProvider
from voiceroom.adapters.base import AudioOut
from voiceroom.audio import StreamResampler, resample
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import make_conversation, split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.io import MIC_QUEUE_FRAMES, FileSource, MicSource, SpeakerSink, WavSink, list_devices
from voiceroom.matcher import Matcher
from voiceroom.pipeline import Pipeline
from voiceroom.policy import IsolationPolicy
from voiceroom.segmenter import Segmenter
from voiceroom.vad import EnergyVAD

devs = list_devices()  # must not crash with no devices / no sounddevice
assert isinstance(devs, list)
print(f"devices: {len(devs)}")

corpus = SynthCorpus(2, seed=1, utts_per_speaker=12)
spks = corpus.speakers()
emb = get_embedder("toy")
enroll_utts, _ = split_utterances(corpus, spks, n_enroll=6)
sigs = [enroll(s, np.concatenate([u.load() for u in enroll_utts[s]]), emb) for s in spks]
conv = make_conversation(
    corpus, spks, n_turns=4, pause_range=(0.6, 1.2), snr_db=None, seed=3, n_enroll=6
)

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    in_wav, out_wav = d / "in.wav", d / "out.wav"
    sf.write(in_wav, conv.audio, 16000)
    provider = MockProvider()
    # threshold -1 / owner = first speaker: every turn is committed, so replies are produced.
    pipe = Pipeline(
        EnergyVAD(),
        Segmenter(),
        Matcher(sigs, threshold=-1.0, embedder=emb),
        IsolationPolicy(spks[0], -1.0, embedder="toy"),
        provider,
        max_pending=1000,
        isolation="thread",
    )

    async def main() -> int:
        await pipe.run(FileSource(in_wav))
        await provider.close()
        with WavSink(out_wav, sample_rate=24000) as sink:
            await sink.play(provider.audio_out())
            return sink.samples_written

    n = asyncio.run(main())
    assert provider.turns, "pipeline committed no turns"
    info = sf.info(out_wav)
    assert out_wav.stat().st_size > 1000 and info.frames == n > 0 and info.samplerate == 24000
    print(f"turns={len(provider.turns)} wav frames={info.frames}")

    # Realtime pacing: 0.5 s of audio takes ~0.5 s; fast mode is much quicker.
    async def drain(src) -> float:
        t = time.monotonic()
        count = sum([1 async for _ in src])
        assert count > 0
        return time.monotonic() - t

    half = np.zeros(8000, dtype=np.float32)
    assert asyncio.run(drain(FileSource(half, realtime=True))) >= 0.45
    assert asyncio.run(drain(FileSource(half))) < 0.3

# Chunked streaming resampling matches whole-signal resampling (no per-chunk clicks).
t = np.arange(24000) / 24000
sig = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
sig_full = sig
for sr_out, n_in in [(48000, 24000), (16000, 24000), (44100, 24000), (16000, 23999), (16000, 1)]:
    sig_n = sig_full[:n_in]
    rs = StreamResampler(24000, sr_out)
    got = np.concatenate([rs.push(c) for c in np.array_split(sig_n, min(50, n_in))] + [rs.flush()])
    ref = resample(sig_n, 24000, sr_out)
    assert len(got) == len(ref), (sr_out, len(got), len(ref))
    assert np.abs(got - ref).max() < 1e-3, (sr_out, np.abs(got - ref).max())

# SpeakerSink buffering, reply ids and barge-in work without a device.
sp = SpeakerSink(sample_rate=48000)
sp.write(AudioOut(np.ones(1600, dtype=np.float32), 16000, True, 1))
out = np.zeros((4800, 1), dtype=np.float32)
sp._callback(out, 4800, None, None)
assert out[:, 0].max() > 0.5 and abs(sp.played_ms - 100) <= 1
sp.write(AudioOut(np.ones(16000, dtype=np.float32), 16000, False, 2))
sp._callback(out[:2400], 2400, None, None)  # new reply: counter restarts
assert abs(sp.played_ms - 50) <= 1, sp.played_ms
assert abs(sp.interrupt() - 50) <= 1 and not sp._buf
sp.write(AudioOut(np.ones(1600, dtype=np.float32), 16000, False, 2))  # cancelled reply
assert not sp._buf
sp.write(AudioOut(np.ones(1600, dtype=np.float32), 16000, True, 3))
assert sp._buf
# Default reply_id (0) is playable on a fresh sink; interrupting an unplayed reply reports 0 ms.
fresh = SpeakerSink(sample_rate=16000)
fresh.write(AudioOut(np.ones(160, dtype=np.float32)))
assert fresh._buf
sp2 = SpeakerSink(sample_rate=16000)
sp2.write(AudioOut(np.ones(1600, dtype=np.float32), 16000, True, 1))
sp2._callback(np.zeros((1600, 1), dtype=np.float32), 1600, None, None)
sp2.write(AudioOut(np.ones(1600, dtype=np.float32), 16000, False, 2))
assert sp2.interrupt() == 0


class FakeOut:
    """Output device: pulls blocks from the callback on a thread, like PortAudio."""

    def __init__(self, samplerate, callback, **kw):
        self.cb, self.run, self.heard = callback, True, 0

    def start(self):
        def pull():
            while self.run:
                out = np.zeros((160, 1), dtype=np.float32)
                self.cb(out, 160, None, None)
                self.heard += int((out != 0).sum())
                time.sleep(0.005)

        self.t = threading.Thread(target=pull)
        self.t.start()

    def stop(self):
        self.run = False
        self.t.join()

    close = stop


FakeOut_instances = []


def make_out(**kw):
    FakeOut_instances.append(FakeOut(**kw))
    return FakeOut_instances[-1]


sys.modules["sounddevice"] = types.SimpleNamespace(
    OutputStream=make_out, query_devices=lambda *a, **k: {"default_samplerate": 16000.0}
)


async def play_finite():
    async def chunks():
        for i in range(3):
            yield AudioOut(np.ones(1600, dtype=np.float32), 16000, i == 2, 1)

    with SpeakerSink() as sink:
        await sink.play(chunks())
        assert not sink._buf
        await asyncio.sleep(0.02)  # block in flight when drain returned
        return FakeOut_instances[-1].heard


assert asyncio.run(play_finite()) >= 4800 - 160  # the whole reply reached the device


# MicSource against a fake sounddevice: 16 kHz direct, 48 kHz fallback, stalled consumer.
class FakeStream:
    def __init__(self, samplerate, blocksize, callback, **kw):
        if FakeSD.only_rate and samplerate != FakeSD.only_rate:
            raise RuntimeError("Invalid sample rate")
        self.rate, self.block, self.cb = samplerate, blocksize, callback

    def start(self):
        def feed():
            for _ in range(FakeSD.blocks):
                self.cb(np.full((self.block, 1), 0.25, dtype=np.float32), self.block, None, None)

        threading.Thread(target=feed).start()

    def stop(self):
        pass

    close = stop


class FakeSD:
    only_rate, blocks = None, 20
    InputStream = FakeStream

    @staticmethod
    def query_devices(device=None, kind=None):
        return {"default_samplerate": 48000.0}


sys.modules["sounddevice"] = FakeSD
assert isinstance(list_devices(), list)  # fake lacks a device list; must not raise


async def mic_run(blocks: int, consume: bool):
    FakeSD.blocks = blocks
    mic = MicSource()
    async with mic:
        await asyncio.sleep(0.3)
        n = 0
        if consume:
            mic.stop()
            async for f in mic:
                assert f.shape == (512,) and f.dtype == np.float32
                n += 1
    return n, mic.dropped


FakeSD.only_rate = None
n, _ = asyncio.run(mic_run(20, True))
assert n == 20, n
FakeSD.only_rate = 48000
n, _ = asyncio.run(mic_run(20, True))
assert 17 <= n <= 20, n  # resampler lookahead holds back a few ms
n, dropped = asyncio.run(mic_run(MIC_QUEUE_FRAMES + 50, False))
assert dropped >= 40, dropped


async def stalled_loop():
    FakeSD.only_rate, FakeSD.blocks = None, 2000
    mic = MicSource()
    async with mic:
        time.sleep(0.4)  # blocks the event loop while the device thread keeps producing
        assert len(mic._loop._ready) < 10, len(mic._loop._ready)
        assert len(mic._buf) <= MIC_QUEUE_FRAMES and mic.dropped >= 1000, mic.dropped


asyncio.run(stalled_loop())
print("io ok")
