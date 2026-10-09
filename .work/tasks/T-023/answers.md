# T-023 live test: PARTIAL result (human, 2026-10-09)

Decision: accept as partial. Do not block on the rest; treat barge-in and collaboration as unverified on real audio.

- Environment: macOS 27.0.1, Apple silicon (arm64), Python 3.14.8, headphones (AirPods) for output, MacBook Pro Microphone as input, second voice = a friend on a phone call played through the phone speaker (narrowband).
- Setup + fetch_models OK: yes. OpenAI check: first audio after 0.79 s, reply intelligible: yes.
- Enroll alice / bob: OK, list showed both.
- Isolation (`--no-cohort`, see below): Bob forwarded 0 times (his turns were dropped, either identified as bob or unknown). Alice: 5 turns committed, 3 turns identified as alice but dropped (log turns 2, 6, 11; 11 was only 0.16 s, 2 and 6 were 1.15 s and 1.5 s, i.e. owner false-reject). Per-turn latency about 420-450 ms.
- Barge-in: NOT TESTED.
- Collaboration / attribution: NOT TESTED.
- Echo/feedback: could not judge. The friend's voice was sometimes not picked up at all; likely the narrowband phone audio from a phone speaker, not necessarily the system.

Issues found (please handle as follow-ups):
1. `sounddevice` is not a project dependency, so `voiceroom enroll`/`demo` fail on a Mac with "cannot record". I worked around it with `uv run --with sounddevice`. Add it (e.g. an `audio` extra or a main dependency) and fix docs/demo-macos.md, which wrongly says it ships with the project.
2. `data/cohort-wespeaker-resnet34.npz` is encrypted with the key of the machine that built it (the container), so the demo fails on any other machine with "bad cohort file (cannot decrypt ...)". Fix: build the cohort per machine, or store it unencrypted (it holds no personal data). I ran with `--no-cohort`, so the AS-norm path was not exercised live.
3. Owner false-reject on real audio: 3 of 8 alice-identified turns dropped; consider this in M1 follow-up tuning.
4. The guided script is `scripts/live/run_macos_demo.sh` (`--demo-only` skips setup and enrollment).

## 2026-10-09 14:30
Accept the partial live result in `.work/tasks/T-023/answers.md` and finish the task. Don't block on the rest. Record in the summary that isolation was checked on real audio only with `--no-cohort`, and that barge-in, collaboration/attribution and echo are unverified. Add follow-up tasks at the end of `backlog.md` for the three issues the human found:
