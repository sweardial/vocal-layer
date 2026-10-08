# Review T-002 (audio utilities + synthetic speakers)

`make check`: green (lint + format clean; `smoke_audio.py` PASS in 0.90 s, offline, no network).
Accept criteria: all met. The smoke checks cover 44.1 kHz stereo wav to 16 kHz mono (length ±1, dtype, corr > 0.99), flac load, the PCM round trip and clipping, frame coverage with and without padding, same seed giving identical audio, different speakers giving lower spectral correlation (measured same 0.97 vs different 0.91), and white/pink/brown SNR at 0/10/20 dB within ±1 dB.
Privacy, secrets, deserialization, and adapter leakage: nothing relevant in this diff.

No HIGH findings.

[MEDIUM] src/voiceroom/synth.py:96-100: `add_noise` scales noise by the RMS of the whole input. Silence gets no noise at all (verified: max |out| = 0.0 for zeros), and for mostly-silent input the effective SNR during speech is much higher than requested → T-008 will mix noise into conversations that include pauses. A conversation that is 40 % silence gets an effective speech SNR about 2.2 dB above `snr_db`, and silent pauses or all-silence segments stay perfectly clean. Downstream VAD and bench results would then be optimistic at the "same" nominal SNR → add an optional `ref_power`/`active_mask` argument (or compute power over samples above an energy floor), document which convention is used, and make the pause regions in the conversation noisy.

[LOW] src/voiceroom/audio.py:45-55: `iter_frames` pads with `np.pad(frame, (0, k))`, which pads every axis. For a 2-D (stereo) input the last frame becomes shape (512, 26) instead of (512, 2) (verified) → a caller that passes un-downmixed audio gets a ragged frame and a confusing downstream shape error → assert `x.ndim == 1`, or pad with `((0, k),) + ((0, 0),) * (x.ndim - 1)`.

[LOW] src/voiceroom/audio.py:18-19: when `sr_in == sr_out`, `resample` returns the caller's float32 array itself (verified `b is a`), and `iter_frames` yields views. Every other path returns a new array → a consumer that normalises or gains a frame in place silently changes the source buffer, but only when the rates match. That makes the bug rate-dependent → return `np.array(x, dtype=np.float32, copy=True)` here, or document the aliasing.

[LOW] src/voiceroom/audio.py:35-37: `float_to_pcm16` lets NaN through `np.clip` and casts it to an undefined int16 value with a RuntimeWarning (verified: NaN gives 0) → a NaN from an upstream DSP bug is silently written as PCM instead of failing loudly → `np.nan_to_num` before clipping, or raise if `not np.isfinite(x).all()`.

[LOW] src/voiceroom/synth.py:57: the harmonic count is capped at `min(sr/2, 5000) // f0` using the *mean* f0. With vibrato and jitter (up to about +9 %), the top harmonic goes above Nyquist when `sr < 10 kHz`. For example, at `sr=8000` with speaker 1 (f0 172 Hz), the top harmonic is about 3956 Hz × 1.03+, which aliases → an 8 kHz telephony test signal would contain aliased components → cap at `0.45 * sr / (f0 * (1 + vibrato_depth + 4*jitter))`.

[LOW] src/voiceroom/synth.py:75-93: `noise(kind, n)` with n=0 emits 4 RuntimeWarnings (mean of an empty slice), and with n=1 it returns `[0.]` (zero RMS after mean removal). As a result `add_noise` on a 1-sample input silently adds no noise → when n < 2, return `np.zeros(n, np.float32)` early without warnings, or raise.

[LOW] .work/tasks/T-002/: there is no `plan.md` or `summary.md` → the harness or a reviewer can't check design intent, for example the pink-filter choice or the SNR convention (RMS over the whole signal, which affects the MEDIUM item above) → write both, and add the SNR convention to decisions.md if T-008 relies on it.
