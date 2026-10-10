"""Bench harness on the LiveKit rtc SDK: publishers streaming in realtime and a listener that
notes when the bot's reply audio starts. The bot itself is a `LiveKitRoom`."""

import asyncio
import time
from collections.abc import Awaitable, Callable

import numpy as np

from voiceroom.adapters.livekit_room import make_token
from voiceroom.audio import SAMPLE_RATE

try:
    from livekit import rtc
except ImportError as e:  # the `livekit` extra
    raise ImportError("the LiveKit harness needs the `livekit` extra") from e

FRAME_S = 0.02  # publisher frame size
ONSET_RMS = 0.02  # a reply tone (rms ~0.07) starts at the first listener frame above this
ONSET_GAP_S = 0.1  # ... after this much quiet
JOIN_TIMEOUT_S = 30.0


async def stream_room(
    server,
    room: str,
    tracks: dict[str, tuple[str, np.ndarray]],
    bot_identity: str,
    joined: Callable[[], bool],
    failed: Callable[[], bool],
    finish: Callable[[], Awaitable[None]],
    settle_s: float,
) -> tuple[float, list[float]]:
    """Join `tracks` (name -> (identity, 16 kHz audio)) as publishers and a listener, wait until
    `joined()` (the bot has every track), stream all of them in realtime from one common start,
    wait `settle_s`, then `await finish()` (stops the bot). Returns (start, reply onsets), both
    `time.monotonic()` wall times. `failed()` aborts the wait for the bot."""

    def token(identity: str) -> str:
        return make_token(
            server.url, room, identity, api_key=server.api_key, api_secret=server.api_secret
        )

    heard, onsets = asyncio.Event(), []
    listen_tasks: list[asyncio.Task] = []
    listener = rtc.Room()

    async def listen(track) -> None:
        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
        last = -1e9
        try:
            async for ev in stream:
                x = np.frombuffer(ev.frame.data, dtype=np.int16).astype(np.float32) / 32768
                if float(np.sqrt(np.mean(x**2))) > ONSET_RMS:
                    now = time.monotonic()
                    if now - last > ONSET_GAP_S:
                        onsets.append(now)
                    last = now
        finally:
            await stream.aclose()

    def subscribe(pub, p) -> None:
        if p.identity == bot_identity and pub.kind == rtc.TrackKind.KIND_AUDIO:
            if not pub.subscribed:
                pub.set_subscribed(True)

    listener.on("track_published", subscribe)

    @listener.on("track_subscribed")
    def _sub(track, pub, p):
        if p.identity == bot_identity:
            heard.set()
            listen_tasks.append(asyncio.ensure_future(listen(track)))

    await listener.connect(server.url, token("listener"), rtc.RoomOptions(auto_subscribe=False))
    for p in list(listener.remote_participants.values()):
        for pub in list(p.track_publications.values()):
            subscribe(pub, p)

    pubs: list[tuple[rtc.Room, rtc.AudioSource, np.ndarray]] = []

    async def join(identity: str, audio: np.ndarray) -> None:
        r = rtc.Room()
        await r.connect(server.url, token(identity))
        source = rtc.AudioSource(SAMPLE_RATE, 1)
        mic = rtc.LocalAudioTrack.create_audio_track("mic", source)
        await r.local_participant.publish_track(
            mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        pubs.append((r, source, audio))

    await asyncio.gather(*(join(i, a) for i, a in tracks.values()))
    t0 = time.monotonic()
    try:
        deadline = time.monotonic() + JOIN_TIMEOUT_S
        while not joined() or not heard.is_set():
            if failed() or time.monotonic() > deadline:
                raise RuntimeError("bot did not subscribe to every track in time")
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)  # let the receiver pipelines settle
        t0 = time.monotonic()
        n = int(FRAME_S * SAMPLE_RATE)

        async def stream(source, audio) -> None:
            pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
            pcm = np.pad(pcm, (0, -len(pcm) % n))
            for i in range(0, len(pcm), n):
                await asyncio.sleep(max(0.0, t0 + i / SAMPLE_RATE - time.monotonic()))
                frame = rtc.AudioFrame(pcm[i : i + n].tobytes(), SAMPLE_RATE, 1, n)
                await source.capture_frame(frame)

        await asyncio.gather(*(stream(s, a) for _, s, a in pubs))
        await asyncio.sleep(settle_s)
    finally:
        for _, source, _ in pubs:
            await source.aclose()
        await finish()
        for r, _, _ in pubs:
            await r.disconnect()
        await listener.disconnect()
        for t in listen_tasks:
            t.cancel()
        await asyncio.gather(*listen_tasks, return_exceptions=True)
    return t0, onsets
