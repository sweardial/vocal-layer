"""Audio sources and sinks: live mic/speaker (sounddevice, lazy) and file stand-ins.

Sources are async iterators of 512-sample float32 frames at 16 kHz (what `Pipeline.run` eats).
Sinks take `AudioOut` chunks from a provider. Live I/O needs the optional `sounddevice` package
(`uv pip install sounddevice`) and PortAudio; nothing here imports it at module load.
"""

import asyncio
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import soundfile as sf

from voiceroom.adapters.base import AudioOut
from voiceroom.audio import SAMPLE_RATE, StreamResampler, iter_frames, load_audio, resample, to_mono
from voiceroom.vad import FRAME_SIZE

MIC_QUEUE_FRAMES = 500  # ~32 s; beyond this the oldest frames are dropped (consumer stalled)
MAX_SPEAKER_BUFFER_S = 60.0


def _sounddevice():
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:  # OSError: PortAudio library missing
        raise RuntimeError(
            "live audio needs the 'sounddevice' package and PortAudio (uv pip install sounddevice)"
        ) from exc
    return sd


def list_devices() -> list[dict]:
    """Audio devices as dicts (index, name, max_input_channels, max_output_channels).

    Returns [] when sounddevice/PortAudio is missing or no devices exist; never raises.
    """
    try:
        devices = _sounddevice().query_devices()
        return [
            {
                "index": i,
                "name": str(d["name"]),
                "max_input_channels": int(d["max_input_channels"]),
                "max_output_channels": int(d["max_output_channels"]),
            }
            for i, d in enumerate(devices)
        ]
    except Exception:
        return []


class FileSource:
    """Frames from an audio file (or array). `realtime=True` paces frames at wall-clock speed.

    Arrays are taken as mono at `sample_rate` (stereo is averaged) and resampled to 16 kHz.
    """

    def __init__(
        self,
        source: str | Path | np.ndarray,
        realtime: bool = False,
        frame_size: int = FRAME_SIZE,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        if isinstance(source, str | Path):
            audio = load_audio(source)
        else:
            audio = resample(to_mono(np.asarray(source, dtype=np.float32)), sample_rate)
        self.audio = audio
        self.realtime, self.frame_size = realtime, frame_size

    def __aiter__(self) -> AsyncIterator[np.ndarray]:
        return self._frames()

    async def _frames(self) -> AsyncIterator[np.ndarray]:
        period = self.frame_size / SAMPLE_RATE
        start = time.monotonic()
        for i, frame in enumerate(iter_frames(self.audio, self.frame_size)):
            if self.realtime:
                delay = start + i * period - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
            elif i % 64 == 0:
                await asyncio.sleep(0)  # let the pipeline's worker task run
            yield frame


class MicSource:
    """Live microphone frames (16 kHz mono) via a sounddevice InputStream.

    Use as `async with MicSource() as mic: await pipe.run(mic)`. The PortAudio callback hands
    frames to the event loop; if the consumer falls behind, the oldest frames are dropped.
    Devices that refuse 16 kHz are opened at their default rate and resampled.
    """

    def __init__(self, device: int | str | None = None, frame_size: int = FRAME_SIZE) -> None:
        self.device, self.frame_size = device, frame_size
        # Bounded cross-thread buffer + at most one pending loop wake-up, so a stalled event
        # loop cannot accumulate callbacks or stale audio beyond MIC_QUEUE_FRAMES.
        self._buf: deque[np.ndarray] = deque(maxlen=MIC_QUEUE_FRAMES)
        self._mlock = threading.Lock()
        self._wake_pending = False
        self._closed = False
        self._event = asyncio.Event()
        self._stream = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._rs: StreamResampler | None = None
        self._pending = np.zeros(0, dtype=np.float32)
        self.dropped = 0  # frames lost to a slow consumer or device overflow

    async def __aenter__(self) -> "MicSource":
        self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        sd = _sounddevice()
        self._loop = asyncio.get_running_loop()
        self._pending = np.zeros(0, dtype=np.float32)
        self._closed = False
        try:
            self._stream = self._open(sd, SAMPLE_RATE)
            self._rs = None
        except Exception:
            rate = int(sd.query_devices(self.device, "input")["default_samplerate"])
            self._rs = StreamResampler(rate, SAMPLE_RATE)
            self._stream = self._open(sd, rate)
        self._stream.start()

    def _open(self, sd, rate: int):
        block = (
            self.frame_size if rate == SAMPLE_RATE else int(self.frame_size * rate / SAMPLE_RATE)
        )
        return sd.InputStream(
            samplerate=rate,
            channels=1,
            dtype="float32",
            blocksize=block,
            device=self.device,
            callback=self._callback,
        )

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            with self._mlock:
                self._closed = True
            self._notify()

    def _callback(self, indata, frames, time_info, status) -> None:
        # PortAudio thread: copy (the buffer is reused), resample if needed, buffer, notify.
        if status and getattr(status, "input_overflow", False):
            self.dropped += 1
        x = indata[:, 0].copy()
        if self._rs is not None:
            x = self._rs.push(x)
        self._pending = np.concatenate([self._pending, x])
        while len(self._pending) >= self.frame_size:
            with self._mlock:
                if len(self._buf) == self._buf.maxlen:
                    self.dropped += 1  # deque drops the oldest frame
                self._buf.append(self._pending[: self.frame_size])
            self._pending = self._pending[self.frame_size :]
            self._notify()

    def _notify(self) -> None:
        with self._mlock:
            if self._wake_pending:
                return
            self._wake_pending = True
        try:
            self._loop.call_soon_threadsafe(self._wake)
        except RuntimeError:  # loop already closed
            pass

    def _wake(self) -> None:
        with self._mlock:
            self._wake_pending = False
        self._event.set()

    def __aiter__(self) -> AsyncIterator[np.ndarray]:
        return self._frames()

    async def _frames(self) -> AsyncIterator[np.ndarray]:
        if self._stream is None and not self._buf and not self._closed:
            raise RuntimeError("MicSource not started: use 'async with MicSource()' or start()")
        while True:
            with self._mlock:
                frame = self._buf.popleft() if self._buf else None
                closed = self._closed
            if frame is not None:
                yield frame
                continue
            if closed:
                return
            self._event.clear()
            with self._mlock:
                if self._buf or self._closed:
                    continue
            await self._event.wait()


class _ChunkResampler:
    """Per-reply streaming resampler for AudioOut chunks; flushes on `final`."""

    def __init__(self, sr_out: int) -> None:
        self.sr_out, self._rs, self._sr_in = sr_out, None, None

    def __call__(self, chunk: AudioOut) -> np.ndarray:
        if self._rs is None or self._sr_in != chunk.sample_rate:
            tail = self.flush()
            self._rs, self._sr_in = (
                StreamResampler(chunk.sample_rate, self.sr_out),
                chunk.sample_rate,
            )
            head = self._rs.push(to_mono(np.asarray(chunk.samples, dtype=np.float32)))
            out = np.concatenate([tail, head])
        else:
            out = self._rs.push(to_mono(np.asarray(chunk.samples, dtype=np.float32)))
        if chunk.final:
            out = np.concatenate([out, self.flush()])
        return out

    def flush(self) -> np.ndarray:
        return self._rs.flush() if self._rs else np.zeros(0, dtype=np.float32)

    def reset(self) -> None:
        if self._rs:
            self._rs.reset()


class SpeakerSink:
    """Plays provider audio through the default (or given) output device, resampling to it.

    `write()` queues audio (dropping chunks of replies already interrupted); `interrupt()`
    drops what is queued (barge-in) and returns ms of the current reply that reached the
    device, for the provider's truncation.
    """

    def __init__(self, device: int | str | None = None, sample_rate: int | None = None) -> None:
        self.device, self.sample_rate = device, sample_rate
        self._buf: deque[tuple[int, np.ndarray]] = deque()  # (reply_id, samples at device rate)
        self._buffered = 0
        self._lock = threading.Lock()
        self._played = 0  # device-rate samples of reply `_playing` played so far
        self._playing = -1
        self._latest = -1  # newest reply_id written
        self._cancelled = -1  # replies with id <= this were interrupted
        self._rs: _ChunkResampler | None = None
        self._stream = None

    def __enter__(self) -> "SpeakerSink":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        sd = _sounddevice()
        if self.sample_rate is None:
            self.sample_rate = int(sd.query_devices(self.device, "output")["default_samplerate"])
        self._rs = _ChunkResampler(self.sample_rate)
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _ensure_rate(self) -> None:
        if self.sample_rate is None:
            raise RuntimeError("SpeakerSink not started")
        if self._rs is None:  # sample_rate given up front and no device (headless use)
            self._rs = _ChunkResampler(self.sample_rate)

    def write(self, chunk: AudioOut) -> None:
        """Queue a chunk. Resampling happens outside the lock the device callback takes."""
        self._ensure_rate()
        with self._lock:
            if chunk.reply_id <= self._cancelled:
                return
            new_reply = chunk.reply_id != self._latest
            self._latest = chunk.reply_id
        if new_reply:
            self._rs.reset()
        x = self._rs(chunk)
        with self._lock:
            if chunk.reply_id <= self._cancelled:  # interrupted while resampling
                return
            if self._buffered + len(x) > MAX_SPEAKER_BUFFER_S * self.sample_rate:
                return  # consumer far behind; drop rather than grow without bound
            if len(x):
                self._buf.append((chunk.reply_id, x))
                self._buffered += len(x)

    def interrupt(self) -> int:
        """Drop queued audio and cancel the newest reply; returns ms of it already played.

        0 if that reply has not reached the device yet (earlier replies' time is not reported).
        """
        with self._lock:
            ms = self.played_ms if self._playing == self._latest else 0
            self._cancelled = max(self._cancelled, self._latest, self._playing)
            self._buf.clear()
            self._buffered = 0
            self._played = 0
        if self._rs:
            self._rs.reset()
        return ms

    @property
    def played_ms(self) -> int:
        return int(1000 * self._played / (self.sample_rate or SAMPLE_RATE))

    def _callback(self, outdata, frames, time_info, status) -> None:
        out = np.zeros(frames, dtype=np.float32)
        n = 0
        # Never wait on a producer: if the lock is busy, emit silence for this block.
        if self._lock.acquire(blocking=False):
            try:
                while n < frames and self._buf:
                    rid, head = self._buf[0]
                    if rid != self._playing:
                        self._playing, self._played = rid, 0  # a new reply reached the device
                    take = min(len(head), frames - n)
                    out[n : n + take] = head[:take]
                    n += take
                    self._played += take
                    if take == len(head):
                        self._buf.popleft()
                    else:
                        self._buf[0] = (rid, head[take:])
                self._buffered -= n
            finally:
                self._lock.release()
        outdata[:, 0] = out

    async def drain(self, poll_s: float = 0.01) -> None:
        """Wait until queued audio has been handed to the device (returns at once on
        `interrupt()` or when no output stream is running)."""
        while self._stream is not None and self._buf:
            await asyncio.sleep(poll_s)

    async def play(self, chunks: AsyncIterator[AudioOut]) -> None:
        """Play a finite stream to completion (use `write` + `interrupt` for live barge-in)."""
        async for c in chunks:
            self.write(c)
        await self.drain()


class WavSink:
    """Writes provider audio to a mono 16-bit wav at `sample_rate`, resampling as needed."""

    def __init__(self, path: str | Path, sample_rate: int = SAMPLE_RATE) -> None:
        self.path, self.sample_rate = Path(path), sample_rate
        self.samples_written = 0
        self._rs = _ChunkResampler(sample_rate)
        self._f = sf.SoundFile(
            str(self.path), "w", samplerate=sample_rate, channels=1, subtype="PCM_16"
        )

    def write(self, chunk: AudioOut) -> None:
        x = self._rs(chunk)
        self._f.write(np.clip(x, -1.0, 1.0))
        self.samples_written += len(x)

    async def play(self, chunks: AsyncIterator[AudioOut]) -> None:
        async for c in chunks:
            self.write(c)

    def close(self) -> None:
        if not self._f.closed:
            tail = self._rs.flush()
            self._f.write(np.clip(tail, -1.0, 1.0))
            self.samples_written += len(tail)
            self._f.close()

    def __enter__(self) -> "WavSink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
