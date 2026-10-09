# macOS live demo checklist

Verifies enrollment, isolation, collaboration, and the live OpenAI path with a real mic and speaker.
Needs two people in the same room (call them **Alice**, the owner, and **Bob**). Time: ~20 minutes.
The live steps call the OpenAI Realtime API and cost a little money.

## 0. Prerequisites

- Apple silicon or Intel Mac, macOS 13+, a working mic and speaker.
- **Headphones for Alice (strongly advised).** Speaker output leaking into the mic is picked up as
  a "speaker" and can trigger false barge-in or replies to itself. If you use laptop speakers,
  lower the volume and keep the model's output quiet; echo failures are a known limit, so note
  them in the results rather than treating them as a bug.
- `uv` (`brew install uv`) and Python 3.12 (`uv python install 3.12`).
- `sounddevice` (PortAudio is bundled in the macOS wheel) is a normal project dependency; `uv sync` installs it.
- The shipped cohort file (`data/cohort-*.npz`) only decrypts on the machine that built it (T-034);
  until fixed, add `--no-cohort` to `demo`.
- `scripts/live/run_macos_demo.sh` is a guided version of this checklist (`--demo-only` skips setup and enrollment).
- Grant your terminal app microphone access (System Settings > Privacy & Security > Microphone).
  Without it, recordings are silent.
- An API key: `export OPENAI_API_KEY=sk-...` (same shell as the commands below).
- A quiet room.

## 1. Setup

```bash
uv sync
uv run python scripts/fetch_models.py      # downloads wespeaker + silero into models/
ls models/                                  # expect wespeaker*.onnx and silero_vad.onnx
uv run voiceroom --help
```

Use a throwaway store so nothing real is touched:

```bash
export VOICEROOM_STORE=$(mktemp -d "${TMPDIR:-/tmp}/vr-demo.XXXXXX")   # fresh, private; also passed as --store
export DEMO_AUDIO=$(mktemp -d "${TMPDIR:-/tmp}/vr-demo-audio.XXXXXX")   # test clip + reply go here
```

If the default mic or speaker is wrong, pass `--input-device` / `--output-device` (index or name)
to `enroll` / `demo`.

Keep using the same terminal window for the whole checklist (the two variables above are only set there).

## 2. Enrollment (twice)

Each enrollment records ~25 s. Speak naturally the whole time (read a paragraph aloud).
Answer `y` at the consent prompt.

```bash
uv run voiceroom enroll --store "$VOICEROOM_STORE" --name alice     # Alice speaks
uv run voiceroom enroll --store "$VOICEROOM_STORE" --name bob       # Bob speaks
uv run voiceroom list --store "$VOICEROOM_STORE"                    # expect both names
```

Expected: each command succeeds, `list` shows `alice` and `bob`. Nothing audio-like is written to
the store (encrypted signatures only).

## 3. Isolation demo (live OpenAI)

```bash
uv run voiceroom demo --store "$VOICEROOM_STORE" --mode isolation --owner alice --provider openai --no-cohort
```

Script (leave a ~1 s pause between lines; Alice wears headphones):

| # | Who   | Says                                          | Expected                                                     |
|---|-------|-----------------------------------------------|--------------------------------------------------------------|
| 1 | Alice | "Hi, what is the capital of France?"          | A turn is logged as `speaker=alice` and forwarded; model answers aloud. |
| 2 | Bob   | "Tell me a joke about cats."                  | Logged as `speaker=bob` (or unknown) and **dropped**; no model reply. |
| 3 | Alice | "And what is its population?"                 | Forwarded; model answers (context from line 1 is kept).      |
| 4 | Alice | Ask for a long story; ~2 s into the reply, Alice says "stop, wait" | Playback stops promptly (barge-in) and the new turn is handled. |
| 5 | Bob   | Talks while the model is replying             | Playback is **not** interrupted by Bob.                      |

Stop with Ctrl-C (should print `stopped` and exit cleanly). Note the per-turn log lines (speaker,
score, forwarded/dropped) and how long replies take to start.

## 4. Collaboration demo (live OpenAI)

```bash
uv run voiceroom demo --store "$VOICEROOM_STORE" --mode collaboration --provider openai --no-cohort
# optional second run: add --unknown label   (strangers get a generic label instead of being dropped)
```

Script:

| # | Who   | Says                                                           | Expected                                          |
|---|-------|----------------------------------------------------------------|---------------------------------------------------|
| 1 | Alice | "I think we should launch on Monday."                          | Logged `speaker=alice`; model replies or acknowledges. |
| 2 | Bob   | "I disagree, Friday is better. What do you think, assistant?"  | Logged `speaker=bob`.                             |
| 3 | Alice | "Who suggested Monday?"                                        | Model answers that Alice did (attribution works). |
| 4 | Bob   | "Who suggested Friday?"                                        | Model answers that Bob did.                       |

Ctrl-C to stop. Without `--unknown label`, a third unenrolled person's speech should be dropped.

## 5. Raw OpenAI path check

Make a test clip, then send it:

```bash
say -o "$DEMO_AUDIO/hello.wav" --data-format=LEI16@16000 "Hello, can you hear me? Please say a short greeting."
uv run python scripts/live/openai_realtime_check.py --wav "$DEMO_AUDIO/hello.wav" --label alice --out "$DEMO_AUDIO/reply.wav"
afplay "$DEMO_AUDIO/reply.wav"      # only if the command above succeeded
```

Expected: prints `first audio after N.NNs`, writes `$DEMO_AUDIO/reply.wav`, and the reply is
intelligible speech.

## 6. Cleanup

```bash
uv run voiceroom delete --store "$VOICEROOM_STORE" --name alice
uv run voiceroom delete --store "$VOICEROOM_STORE" --name bob
rm -rf "$VOICEROOM_STORE" "$DEMO_AUDIO"
```

## 7. What to report back

Fill in the results form in `.work/tasks/T-023/needs_human.md` (or reply with the same fields):

- macOS version, chip, Python version, headphones or speakers, mic used.
- Any step that failed, with the exact command and the last ~20 lines of output (never paste audio
  or signature files).
- Isolation: was Bob ever forwarded? Was Alice ever dropped? (counts out of ~5 tries each)
- Barge-in: did Alice interrupt playback? Did Bob? Delay in seconds.
- Collaboration: was speaker attribution correct?
- Latency: rough seconds from end of speech to first audio, and the `first audio after` value from step 5.
- Echo or feedback problems, and whether headphones fixed them.
- Anything confusing in this checklist.
