"""Smoke: LiveKitRoom against the local livekit-server. Two publisher clients stream synthetic
speakers (one claims its identity via the `voiceroom_user` attribute), the bot (RoomAgent +
MockProvider, toy embedder) commits their turns, and a listener receives the bot's reply audio.
SKIPs if the binary or the `livekit` extra is missing."""

import asyncio
import gc
import sys
import time

import numpy as np

from voiceroom.adapters import MockProvider
from voiceroom.adapters.livekit_server import find_binary, run_dev_server
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.matcher import Matcher
from voiceroom.policy import TrackPolicy
from voiceroom.room import RoomPipeline
from voiceroom.rooms import RoomAgent
from voiceroom.vad import EnergyVAD

SKIP = 77
try:
    from livekit import rtc

    from voiceroom.adapters.livekit_room import LiveKitRoom, make_token
except ImportError:
    print("SKIP: `livekit` extra not installed")
    sys.exit(SKIP)
if find_binary() is None:
    print("SKIP: livekit-server not found (scripts/fetch_livekit.py)")
    sys.exit(SKIP)

SR = 16000
BUDGET_S = 15.0
corpus = SynthCorpus(2, seed=1, utts_per_speaker=12)
spks = corpus.speakers()
emb = get_embedder("toy")
enroll_utts, held = split_utterances(corpus, spks, n_enroll=6)
sigs = [enroll(s, np.concatenate([u.load() for u in enroll_utts[s]]), emb) for s in spks]
cal = Matcher(sigs, threshold=0.0)
gen, imp = [], []
for s in spks:
    for u in held[s]:
        sc = cal.identify_embedding(emb.embed(u.load()[:SR])).scores
        gen.append(sc[s])
        imp += [v for k, v in sc.items() if k != s]
threshold = (float(np.percentile(gen, 5)) + float(np.percentile(imp, 95))) / 2


def track(spk: str, idxs: list[int]) -> np.ndarray:
    parts = []
    for i in idxs:
        parts += [np.zeros(int(0.5 * SR), np.float32), held[spk][i].load()[: int(2.0 * SR)]]
    return np.concatenate(parts + [np.zeros(SR, np.float32)]).astype(np.float32)


async def publish(server, identity: str, attrs: dict, audio: np.ndarray) -> rtc.Room:
    room = rtc.Room()
    await room.connect(server.url, make_token(server.url, "r", identity, attrs))
    source = rtc.AudioSource(SR, 1)
    mic = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(
        mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    room._smoke_source = source
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    n = SR // 100

    async def stream() -> None:
        for i in range(0, len(pcm) - n + 1, n):
            await source.capture_frame(rtc.AudioFrame(pcm[i : i + n].tobytes(), SR, 1, n))

    room._smoke_task = asyncio.create_task(stream())
    return room


async def main() -> None:
    t0 = time.monotonic()
    provider = MockProvider()
    pipeline = RoomPipeline(
        provider,
        lambda sid, claimed: TrackPolicy(
            "verified",
            spks,
            claimed,
            threshold=threshold,
            verify_threshold=threshold - 0.02,
            window_drop=0.05,
        ),  # fmt: skip
        Matcher(sigs, threshold=threshold, embedder=emb),
        vad_factory=EnergyVAD,
        max_pending=1000,
        max_inflight_identify=1000,
        isolation="thread",
    )
    with run_dev_server() as server:
        bot = LiveKitRoom(server.url, make_token(server.url, "r", "bot"))
        agent_task = asyncio.create_task(RoomAgent(bot, pipeline, provider).run())

        heard: list[np.ndarray] = []
        listener = rtc.Room()
        listen_tasks: list[asyncio.Task] = []
        listener_ready = asyncio.Event()

        async def listen(track: rtc.Track) -> None:
            stream = rtc.AudioStream(track, sample_rate=SR, num_channels=1)
            try:
                async for ev in stream:
                    heard.append(np.frombuffer(ev.frame.data, dtype=np.int16))
            finally:
                await stream.aclose()

        @listener.on("track_subscribed")
        def on_sub(track, pub, participant):
            if participant.identity == "bot":
                listener_ready.set()
                listen_tasks.append(asyncio.ensure_future(listen(track)))

        await listener.connect(server.url, make_token(server.url, "r", "listener"))
        # alice by plain identity; the second publisher's identity differs from its claimed one
        a = await publish(server, spks[0], {}, track(spks[0], [0, 1]))
        b = await publish(server, "device-7", {"voiceroom_user": spks[1]}, track(spks[1], [0, 1]))
        try:
            deadline = t0 + BUDGET_S - 1
            while time.monotonic() < deadline and not (
                len(provider.turns) >= 4 and sum(map(len, heard)) >= 0.25 * SR
            ):
                assert not agent_task.done(), agent_task
                await asyncio.sleep(0.1)
        finally:
            await asyncio.gather(a._smoke_task, b._smoke_task, return_exceptions=True)
            for r in (a, b):
                await r._smoke_source.aclose()
            await bot.close()  # ends the event stream; the agent drains and stops
            await asyncio.wait_for(agent_task, 10)
            for r in (a, b, listener):
                await r.disconnect()
            for t in listen_tasks:
                t.cancel()
            await asyncio.gather(*listen_tasks, return_exceptions=True)
            del a, b, listener, bot, agent_task
            gc.collect()  # drop FFI handles while the loop still runs (D-018)
            await asyncio.sleep(0.2)

    labels = [t.speaker_label for t in provider.turns]
    assert len(labels) >= 4, labels
    assert set(labels) == set(spks), labels
    assert labels.count(spks[0]) >= 2 and labels.count(spks[1]) >= 2, labels
    assert all(r.verdict.name == "ACCEPT" for r in pipeline.records), pipeline.records
    got = np.concatenate(heard).astype(np.float32) / 32768
    assert len(got) >= 0.25 * SR and np.abs(got).max() > 0.02, (len(got), np.abs(got).max())
    elapsed = time.monotonic() - t0
    assert elapsed < BUDGET_S, f"took {elapsed:.1f}s"
    print(f"PASS: turns {labels}, reply {len(got) / SR:.2f}s heard by listener, {elapsed:.1f}s")


asyncio.run(main())
