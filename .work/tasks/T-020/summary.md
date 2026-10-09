# T-020 summary
**Changed**
- `src/voiceroom/io.py`: `list_devices` (returns [] without sounddevice/devices), `FileSource` (realtime/fast), `MicSource` (16 kHz, falls back to device rate + resample, drop-oldest queue), `SpeakerSink` (device-rate resampling, reply-id aware, `interrupt()` returns ms played of current reply), `WavSink`. sounddevice imported lazily.
- `src/voiceroom/audio.py`: `StreamResampler` (click-free chunked resampling).
- `scripts/smoke/smoke_io.py`: FileSource -> Pipeline(Mock) -> WavSink; pacing; chunked-vs-whole resampling; SpeakerSink logic; MicSource via a fake `sounddevice`.

**Verified**: `make check` green (20 scripts, 0 failed). Reviewer HIGH (per-chunk resample clicks) and MEDIUMs (played_ms reset, reply_id drop, mic rate fallback, mic smoke) fixed.

**Follow-ups / notes**
- `sounddevice` is not declared in pyproject (adding an extra needs a network lock update); add `live = ["sounddevice"]` when online.
- Real PortAudio paths are untested in the container (fake module only).
- Total smoke runtime is ~62 s, slightly over the 60 s policy (pre-existing; slowest are other scripts).

## Fix round 2
- MicSource: bounded deque (drop-oldest) + at most one pending loop wake-up; stalled-loop scenario added to smoke.
- SpeakerSink: resampling outside the callback lock; callback uses non-blocking acquire (silence if busy); watermarks start at -1 so default reply_id=0 plays; `interrupt()` returns 0 if the newest reply hasn't started playing; `play()` now drains queued audio (`drain()`; skipped when no stream, cut short by `interrupt()`); smoke covers finite-stream completion via a fake output device.
- StreamResampler.flush trims to ceil(n*up/down); smoke covers non-divisible lengths (23999, 1).
- Verified: `make check` green (see run below).
