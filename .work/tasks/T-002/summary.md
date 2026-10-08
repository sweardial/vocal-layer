# T-002 summary
- Added `voiceroom/audio.py` (load_audio, resample, PCM conversion, iter_frames) and `voiceroom/synth.py` (seeded speaker profile, utterance, white/pink/brown noise, add_noise).
- SNR convention: mean power over the whole input, or an explicit `ref_power` (use it for inputs with pauses, e.g. T-008 conversations).
- Added `scripts/smoke/smoke_audio.py`.
- Review: fixed the MEDIUM (`ref_power`) and LOWs (mono assert, copy on same-rate resample, NaN check, tiny-n noise). Skipped: harmonic cap at low sr, since sr is always 16 kHz here.
- Verified: `make check` green (lint + 2 smoke scripts).
