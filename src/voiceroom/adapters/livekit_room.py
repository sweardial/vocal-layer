"""LiveKitRoom: `RoomTransport` on the LiveKit rtc SDK. The bot joins as a participant,
subscribes to every other participant's audio track and publishes provider audio as its own.

Inbound audio is resampled to 16 kHz mono by the SDK (`AudioStream`, proven in T-044), outbound
audio goes through `rtc.AudioSource` at 24 kHz (our `StreamResampler` converts other rates).
The token and API secret are never logged or shown in `repr`.
"""

import asyncio
import contextlib
import logging
import os
import warnings
from collections.abc import AsyncIterator
from datetime import timedelta
from urllib.parse import urlparse

import numpy as np

from voiceroom.adapters.base import AudioOut
from voiceroom.audio import StreamResampler, float_to_pcm16, pcm16_to_float
from voiceroom.rooms.base import ParticipantJoined, ParticipantLeft, RoomEvent, TrackAudio

try:
    from livekit import api, rtc
except ImportError as e:  # the `livekit` extra
    raise ImportError("LiveKitRoom needs the `livekit` extra: uv sync --extra livekit") from e

log = logging.getLogger(__name__)

IN_RATE = 16000
OUT_RATE = 24000  # provider audio (OpenAI realtime) is 24 kHz
FRAME_MS = 10
DEFAULT_TTL_S = 6 * 3600.0
TRACK_QUEUE_CHUNKS = 500  # ~5 s of 10 ms frames; beyond that the oldest audio is dropped
DEV_URL = "ws://127.0.0.1:7880"
DEV_KEY, DEV_SECRET = "devkey", "secret"  # `livekit-server --dev`; only used for localhost
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _is_local(url: str) -> bool:
    return urlparse(url).hostname in _LOCAL_HOSTS


def make_token(
    url: str,
    room: str,
    identity: str,
    attributes: dict[str, str] | None = None,
    *,
    api_key: str | None = None,
    api_secret: str | None = None,
    ttl_s: float | None = None,
) -> str:
    """Join token for `room`. Keys default to $LIVEKIT_API_KEY/$LIVEKIT_API_SECRET; the
    `--dev` pair is allowed only for a localhost `url`."""
    key = api_key or os.environ.get("LIVEKIT_API_KEY")
    secret = api_secret or os.environ.get("LIVEKIT_API_SECRET")
    if not key or not secret:
        if not _is_local(url):
            raise ValueError("set LIVEKIT_API_KEY and LIVEKIT_API_SECRET for a non-local server")
        key, secret = DEV_KEY, DEV_SECRET
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="The HMAC key")  # the dev secret is short
        return (
            api.AccessToken(key, secret)
            .with_identity(identity)
            .with_attributes(attributes or {})
            .with_grants(api.VideoGrants(room_join=True, room=room))
            .with_ttl(timedelta(seconds=ttl_s or DEFAULT_TTL_S))
            .to_jwt()
        )


class LiveKitRoom:
    def __init__(self, url: str, token: str, *, bot_name: str = "voiceroom-bot") -> None:
        self._url, self._token, self._bot_name = url, token, bot_name
        self._room = rtc.Room()
        self._events: asyncio.Queue[RoomEvent | None] = asyncio.Queue()
        self._source: rtc.AudioSource | None = None
        self._track: rtc.LocalAudioTrack | None = None
        self._resamplers: dict[int, StreamResampler] = {}
        self._reply_id = -1
        self._announced: set[str] = set()  # participant sids with a ParticipantJoined emitted
        self._tracks: dict[str, tuple[asyncio.Queue, asyncio.Task]] = {}  # track sid -> pump
        self._closed = False

    def __repr__(self) -> str:
        return f"LiveKitRoom(url={self._url!r})"

    @classmethod
    def from_env(cls, room: str, identity: str = "voiceroom-bot") -> "LiveKitRoom":
        """Build from $LIVEKIT_URL/$LIVEKIT_API_KEY/$LIVEKIT_API_SECRET (localhost dev defaults)."""
        url = os.environ.get("LIVEKIT_URL") or DEV_URL
        return cls(url, make_token(url, room, identity))

    async def connect(self) -> None:
        r = self._room
        r.on("participant_connected", self._on_participant)
        r.on("participant_disconnected", self._on_left)
        r.on("track_published", self._on_published)
        r.on("track_subscribed", self._on_subscribed)
        r.on("track_unsubscribed", self._on_unsubscribed)
        r.on("disconnected", lambda *_: self._end())
        # Audio only: video/data tracks are never subscribed.
        await r.connect(self._url, self._token, rtc.RoomOptions(auto_subscribe=False))
        try:
            self._source = rtc.AudioSource(OUT_RATE, 1)
            self._track = rtc.LocalAudioTrack.create_audio_track(self._bot_name, self._source)
            await r.local_participant.publish_track(
                self._track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            )
            for p in list(r.remote_participants.values()):
                self._on_participant(p)
                for pub in list(p.track_publications.values()):
                    self._on_published(pub, p)
        except BaseException:
            await self.close()
            raise

    # -- inbound -------------------------------------------------------------------------

    def _is_self(self, p: rtc.Participant) -> bool:
        return p.identity == self._room.local_participant.identity

    def _on_participant(self, p: rtc.RemoteParticipant) -> None:
        if self._closed or self._is_self(p) or p.sid in self._announced:
            return
        self._announced.add(p.sid)
        self._events.put_nowait(ParticipantJoined(p.sid, p.identity or None, dict(p.attributes)))

    def _on_published(self, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        if pub.kind == rtc.TrackKind.KIND_AUDIO and not self._is_self(p) and not pub.subscribed:
            pub.set_subscribed(True)

    def _on_subscribed(self, track: rtc.Track, pub, p: rtc.RemoteParticipant) -> None:
        if track.kind != rtc.TrackKind.KIND_AUDIO or self._is_self(p) or self._closed:
            return
        self._on_participant(p)
        q: asyncio.Queue = asyncio.Queue(TRACK_QUEUE_CHUNKS)
        task = asyncio.create_task(self._pump(track, q))
        self._tracks[track.sid] = (q, task)
        self._events.put_nowait(TrackAudio(p.sid, _drain(q)))

    def _on_unsubscribed(self, track: rtc.Track, pub, p: rtc.RemoteParticipant) -> None:
        entry = self._tracks.pop(track.sid, None)
        if entry is not None:
            entry[1].cancel()
            _put_latest(entry[0], None)

    def _on_left(self, p: rtc.RemoteParticipant) -> None:
        if p.sid in self._announced:
            self._announced.discard(p.sid)
            self._events.put_nowait(ParticipantLeft(p.sid))

    async def _pump(self, track: rtc.Track, q: asyncio.Queue) -> None:
        stream = rtc.AudioStream(track, sample_rate=IN_RATE, num_channels=1)
        try:
            async for ev in stream:
                pcm = np.frombuffer(ev.frame.data, dtype=np.int16)
                if _put_latest(q, pcm16_to_float(pcm)):
                    log.warning("track %s: pipeline behind, dropped old audio", track.sid)
        finally:
            await stream.aclose()
            _put_latest(q, None)

    def _end(self) -> None:
        self._events.put_nowait(None)

    async def events(self) -> AsyncIterator[RoomEvent]:
        while (ev := await self._events.get()) is not None:
            yield ev

    # -- outbound ------------------------------------------------------------------------

    async def publish_audio(self, audio: AudioOut) -> None:
        if self._source is None:
            raise RuntimeError("not connected")
        if audio.reply_id != self._reply_id:  # a cut-off reply must not leak into the next
            self._reply_id = audio.reply_id
            self._resamplers.clear()
        rs = self._resamplers.setdefault(
            audio.sample_rate, StreamResampler(audio.sample_rate, OUT_RATE)
        )
        x = rs.push(audio.samples)
        if audio.final:
            x = np.concatenate([x, rs.flush()])
        pcm = float_to_pcm16(x)
        n = OUT_RATE * FRAME_MS // 1000
        for i in range(0, len(pcm), n):
            part = pcm[i : i + n]
            await self._source.capture_frame(rtc.AudioFrame(part.tobytes(), OUT_RATE, 1, len(part)))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for q, task in list(self._tracks.values()):
            task.cancel()
            _put_latest(q, None)
        await asyncio.gather(*(t for _, t in self._tracks.values()), return_exceptions=True)
        self._tracks.clear()
        if self._source is not None:
            with contextlib.suppress(Exception):
                await self._source.aclose()
        with contextlib.suppress(Exception):
            await self._room.disconnect()
        self._end()


def _put_latest(q: asyncio.Queue, item: np.ndarray | None) -> bool:
    """Never block the SDK callback: when full, drop the oldest audio (the end marker wins).
    Returns True if audio was dropped."""
    dropped = False
    while True:
        try:
            q.put_nowait(item)
            return dropped
        except asyncio.QueueFull:
            dropped = True
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()


async def _drain(q: asyncio.Queue) -> AsyncIterator[np.ndarray]:
    while (chunk := await q.get()) is not None:
        yield chunk


def make_kicker(
    url: str,
    room: str,
    *,
    api_key: str | None = None,
    api_secret: str | None = None,
):
    """`async kick(participant_id, identity)` that removes a participant via the LiveKit
    RoomService (credentials as for `make_token`)."""
    key = api_key or os.environ.get("LIVEKIT_API_KEY")
    secret = api_secret or os.environ.get("LIVEKIT_API_SECRET")
    if not key or not secret:
        if not _is_local(url):
            raise ValueError("set LIVEKIT_API_KEY and LIVEKIT_API_SECRET for a non-local server")
        key, secret = DEV_KEY, DEV_SECRET
    http = url.replace("ws://", "http://", 1).replace("wss://", "https://", 1)

    async def kick(participant_id: str, identity: str) -> None:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="The HMAC key")
            lk = api.LiveKitAPI(http, key, secret)
        try:
            await lk.room.remove_participant(
                api.RoomParticipantIdentity(room=room, identity=identity)
            )
        finally:
            await lk.aclose()

    return kick
