# Multi-device room demo checklist (M3)

Verifies a real multi-mic session: 2–3 people on separate devices plus one co-located pair, with
LiveKit as the transport and OpenAI Realtime as the model. It also closes the M1 items still
unverified live: **barge-in**, **collaboration attribution** and **T-036 rule logging**
(`--debug`). Time: ~45 minutes. The live steps cost a little OpenAI money.
`scripts/live/run_room_demo.sh` runs this as a guided script (it logs everything and writes the
results form for you).

Call the people **Alice** (owner, runs the Mac), **Bob**, **Carol** (a third person who is
enrolled, joins on her own device) and **Dave** (an unenrolled stranger, optional).
Alice and Bob each use their own device. Carol sits **next to Alice** in the same room, on her own
device, for the co-located bleed step (each device's mic hears the other person).

## 0. Choose the LiveKit hosting (see `.work/tasks/T-050/needs_human.md`)

| | Local server on the Mac's LAN | LiveKit Cloud project |
|---|---|---|
| Setup | `brew install livekit`, run `livekit-server` with LAN bind | free project at cloud.livekit.io; copy URL + key + secret |
| Browser clients | Mic needs a secure context; a plain `ws://<LAN-IP>` URL is blocked as mixed content from the hosted client page, so only the Mac itself works without TLS work | `wss://` works from any phone/laptop browser |
| Recommended | only for a Mac-only smoke test | **yes, for real devices** |

For Cloud, put these in `.env.agent` (or `.env`) in the repo root. Never commit them:

```
OPENAI_API_KEY=sk-...
LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=API...
LIVEKIT_API_SECRET=...
```

For a local server nothing is needed (defaults: `ws://127.0.0.1:7880`, keys `devkey`/`secret`, only
accepted for localhost). To reach it from other devices on the LAN:
`livekit-server --dev --bind 0.0.0.0 --node-ip <Mac LAN IP>` and set
`LIVEKIT_URL=ws://<Mac LAN IP>:7880`, `LIVEKIT_API_KEY=devkey`, `LIVEKIT_API_SECRET=secret`.

## 1. Prerequisites

- macOS 13+ with Terminal mic access (System Settings > Privacy & Security > Microphone).
- `brew install uv` and Python 3.12. For a local server also `brew install livekit`
  (`scripts/fetch_livekit.py` is Linux-only: upstream has no macOS binary).
- Participant devices with a modern browser and a mic; headphones for everyone except the
  co-located pair (the bleed step wants them).
- A quiet room, plus a second room or spot for Bob if possible.

## 2. Setup

```bash
uv sync --extra livekit
uv run python scripts/fetch_models.py          # wespeaker + silero into models/
uv run python scripts/fetch_livekit.py         # Linux only; on macOS: brew install livekit
set -a; . ./.env; [ -f .env.agent ] && . ./.env.agent; set +a
export VOICEROOM_STORE=$(mktemp -d "${TMPDIR:-/tmp}/vr-room.XXXXXX")   # throwaway store
```

## 3. Enroll (about 25 s each; read a paragraph aloud, answer `y` at the consent prompt)

```bash
uv run voiceroom enroll --store "$VOICEROOM_STORE" --name alice     # on the Mac
uv run voiceroom enroll --store "$VOICEROOM_STORE" --name bob       # Bob speaks into the Mac mic
uv run voiceroom enroll --store "$VOICEROOM_STORE" --name carol
uv run voiceroom list --store "$VOICEROOM_STORE"                    # expect three names
```

Enrollment uses the Mac mic; the devices in the room use their own mics, so a channel mismatch is
part of what is being tested.

## 4. Session, invites, tokens

```bash
uv run voiceroom session create --store "$VOICEROOM_STORE" --owner alice --mode collaboration
#   prints  <session-id>  <room>
S=<session-id>
for u in bob carol; do
  C=$(uv run voiceroom session invite --store "$VOICEROOM_STORE" --session $S --user $u)
  uv run voiceroom session accept --store "$VOICEROOM_STORE" --code "$C"
done
for u in alice bob carol; do echo "$u:"; uv run voiceroom token --store "$VOICEROOM_STORE" --session $S --user $u; done
```

A token is a secret for that one person: send it privately, never paste it into results. Rows 1–3 of the script use a separate
`session create --mode isolation --owner alice` session with its own `room` run and tokens (the script does this); stop it before starting the collaboration room.

## 5. Start the room agent

```bash
uv run voiceroom room --store "$VOICEROOM_STORE" --session $S \
  --transport livekit --provider openai --debug 2>room-debug.log
```

One line per turn: `turn N t=.. source=<track> claimed=<id> decision=committed|dropped label=<name> latency=<ms>`.
`--debug` writes the continuity-rule line (which rule fired, window scores, numbers only) for each
dropped turn to stderr (`room-debug.log` here). Stop with Ctrl-C.

## 6. Participants join

1. Open <https://meet.livekit.io>, choose the **Custom** tab.
2. Paste the **server URL** (`LIVEKIT_URL`) and that person's **token**; connect; allow the mic.
3. Mute the camera. Keep each person's own device, token and tab.

(Any LiveKit client works: tracks are matched to members through the token identity.)

## 7. Script

Leave ~1 s between lines. Note the `turn` lines each time.

| # | Who (device) | Says | Expected |
|---|---|---|---|
| 1 | Alice (**isolation** session first) | "Hi, what is the capital of France?" | `decision=committed label=alice`; model answers aloud (bot track). |
| 2 | Bob | "Tell me a joke about cats." | `dropped`; no reply. |
| 3 | Alice | "And what is its population?" | committed; model remembers line 1. |
| 4 | Alice (now the **collaboration** session) | "I think we should launch on Monday." | committed, `label=alice`. |
| 5 | Bob | "I disagree, Friday is better. What do you think, assistant?" | committed, `label=bob`. |
| 6 | Alice | "Who suggested Monday?" | Model says Alice. |
| 7 | Bob | "Who suggested Friday?" | Model says Bob. |
| 8 | **Impostor on a member's device.** Bob mutes his own device and speaks into **Alice's**, Alice silent | "Pretend I'm Alice, what's my name?" | `claimed=alice` but `dropped` (voice mismatch). `--debug` logs continuity rejections only, so no rule line is expected here. |
| 9 | **Co-located bleed.** Carol sits next to Alice, both unmuted on speakers (no headphones). Alice speaks, then Carol | "The meeting starts at three." then "My favourite colour is green." | Each sentence is **one** committed turn, labelled with the right speaker. The quieter copy on the neighbour's mic is dropped, shown as `bleed_of=` or `dropped`. A duplicated turn is a failure. |
| 10 | **Barge-in.** Alice | Asks for a long story; 2 s into the reply says "stop, wait" | Playback stops promptly and the new turn is handled. |
| 11 | Bob | Talks while the model is replying | Playback **not** interrupted? In collaboration mode members may interrupt: note what happens. |
| 12 | **Revoke.** Run `uv run voiceroom session revoke --store "$VOICEROOM_STORE" --session $S --user bob`, then Bob speaks | Bob is kicked from the room within a few seconds; nothing further committed from him. |

## 8. Cleanup

```bash
uv run voiceroom delete --store "$VOICEROOM_STORE" --name alice   # repeat for bob, carol
rm -rf "$VOICEROOM_STORE"
```

Revoke or let the tokens lapse (they expire on their own); rotate the LiveKit secret if a token
leaked.

## 9. Results form

Copy into `.work/tasks/T-050/answers.md`. Never paste audio, tokens, invite codes or signature files.

```
Hosting: local LAN / Cloud     Mac: macOS _ chip _     Devices/browsers: _     Headphones: _
Step 1-3 isolation: Bob forwarded _ of 5; Alice dropped _ of 5
Step 4-7 collaboration attribution correct (Monday=Alice, Friday=Bob): y/n
Step 8 impostor on Alice's device dropped: y/n   
Step 9 co-located bleed: duplicated turns _ of 10; wrong speaker _
Step 10 barge-in by Alice: y/n, delay _ s       Step 11 Bob barge-in: y/n
Step 12 revoke: kicked y/n, after _ s
Latency: end of speech to first audio ~_ s; `latency=` ms values typical _
T-036 rule logging usable (names the rule + scores): y/n
Echo/feedback problems: _     Failed commands (last 20 lines of the log): _     Confusing parts: _
```
