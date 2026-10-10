"""Smoke: local livekit-server loopback. Client A publishes a 2 s 440 Hz tone, client B (same
process) receives it via AudioStream(sample_rate=16000). SKIPs if the binary or `livekit` extra
is missing. Prints the facts D-018 needs (resampling, ports)."""

import asyncio
import sys
import time
import warnings

import numpy as np

from voiceroom.adapters.livekit_server import find_binary, run_dev_server

SKIP = 77
warnings.filterwarnings("ignore", message="The HMAC key")  # the dev secret is short
try:
    from livekit import api, rtc
except ImportError:
    print("SKIP: `livekit` extra not installed")
    sys.exit(SKIP)

SRC_RATE = 48000
OUT_RATE = 16000
TONE_S = 2.0
BUDGET_S = 10.0


def token(server, identity: str) -> str:
    return (
        api.AccessToken(server.api_key, server.api_secret)
        .with_identity(identity)
        .with_grants(api.VideoGrants(room_join=True, room="loop"))
        .to_jwt()
    )


async def main() -> None:
    t0 = time.monotonic()
    with run_dev_server() as server:
        a, b = rtc.Room(), rtc.Room()
        got: asyncio.Future = asyncio.get_running_loop().create_future()
        samples: list[np.ndarray] = []
        who: list[str] = []

        async def consume(track: rtc.Track, identity: str) -> None:
            stream = rtc.AudioStream(track, sample_rate=OUT_RATE, num_channels=1)
            async for ev in stream:
                f = ev.frame
                assert f.sample_rate == OUT_RATE and f.num_channels == 1, (f.sample_rate,)
                who.append(identity)
                samples.append(np.frombuffer(f.data, dtype=np.int16).astype(np.float32) / 32768)
                if sum(len(s) for s in samples) >= int(1.8 * OUT_RATE) and not got.done():
                    got.set_result(None)

        @b.on("track_subscribed")
        def on_sub(track, pub, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.ensure_future(consume(track, participant.identity))

        await b.connect(server.url, token(server, "listener"))
        await a.connect(server.url, token(server, "alice"))
        source = rtc.AudioSource(SRC_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("mic", source)
        await a.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        n = SRC_RATE // 100
        t = np.arange(int(TONE_S * SRC_RATE)) / SRC_RATE
        tone = (0.3 * 32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
        for i in range(0, len(tone) - n + 1, n):
            await source.capture_frame(rtc.AudioFrame(tone[i : i + n].tobytes(), SRC_RATE, 1, n))
        await source.wait_for_playout()
        try:
            await asyncio.wait_for(got, timeout=3.0)
        finally:
            await source.aclose()
            await a.disconnect()
            await b.disconnect()

    total = np.concatenate(samples)
    secs = len(total) / OUT_RATE
    assert set(who) == {"alice"}, set(who)
    assert secs >= 1.8, f"only {secs:.2f}s received"
    # Dominant frequency must be 440 Hz at 16 kHz (proves real resampling, not relabelling).
    spec = np.abs(np.fft.rfft(total[OUT_RATE // 2 :]))
    peak = np.argmax(spec) * OUT_RATE / (len(total) - OUT_RATE // 2)
    assert abs(peak - 440) < 10, f"peak {peak:.0f} Hz"
    elapsed = time.monotonic() - t0
    assert elapsed < BUDGET_S, f"took {elapsed:.1f}s"
    print(f"PASS: {secs:.2f}s at {OUT_RATE} Hz from {set(who)}, peak {peak:.0f} Hz, {elapsed:.1f}s")


if find_binary() is None:
    print("SKIP: livekit-server not found (scripts/fetch_livekit.py)")
    sys.exit(SKIP)
asyncio.run(main())
