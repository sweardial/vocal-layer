#!/usr/bin/env bash
# Guided multi-device room test (docs/demo-room.md as one script). Run from the repo on the Mac:
#   bash scripts/live/run_room_demo.sh
# Reads OPENAI_API_KEY and optional LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET from the
# environment, ./.env and ./.env.agent. Without LIVEKIT_URL it uses a local `livekit-server --dev`
# on this Mac only. Writes data/room-results.txt (your answers) and data/room-log.txt (commands).
# Join tokens are shown on screen only: they are never written to either file.
set -u
cd "$(dirname "$0")/../.." || exit 1
for f in "${VOICEROOM_ENV_FILE:-.env}" "${VOICEROOM_ENV_AGENT_FILE:-.env.agent}"; do
  if [ -f "$f" ]; then set -a; . "./$f"; set +a; fi
done
OUT="${VOICEROOM_DEMO_OUT:-data}"
mkdir -p "$OUT"
RESULTS=$OUT/room-results.txt
LOG=$OUT/room-log.txt
: >"$RESULTS"; : >"$LOG"
export PYTHONUNBUFFERED=1
unset VOICEROOM_STORE VOICEROOM_SESSIONS
STORE=$(mktemp -d "${TMPDIR:-/tmp}/vr-room.XXXXXX") || exit 1
UVR="uv run"
PIDS=""

say_() { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '%s\n' "$*" >>"$RESULTS"; }
pause() { read -r -p "${1:-Press Enter to continue}" _; }
yn() { local a; while true; do read -r -p "$1 [y/n] " a; case "$a" in y|Y) return 0;; n|N) return 1;; esac; done; }
ask() { local a; read -r -p "$1 " a; note "$1 $a"; }
run() { printf '\n$ %s\n' "$*" >>"$LOG"; "$@" 2>&1 | tee -a "$LOG"; return "${PIPESTATUS[0]}"; }
quiet() { "$@" 2>>"$LOG"; }  # output is a secret (token, invite code): keep it out of the log
cleanup() {
  [ -n "$PIDS" ] && kill $PIDS 2>/dev/null
  for n in alice bob carol; do $UVR voiceroom delete --store "$STORE" --name $n >/dev/null 2>&1; done
  rm -rf "$STORE"
}
trap cleanup EXIT
trap 'exit 130' INT

say_ "0. Prerequisites"
[ "$(uname)" = Darwin ] || { echo "This is for macOS."; exit 1; }
command -v uv >/dev/null || { echo "Install uv first: brew install uv"; exit 1; }
[ -n "${OPENAI_API_KEY:-}" ] || { echo "Set OPENAI_API_KEY in .env or the environment."; exit 1; }
note "Environment: $(sw_vers -productVersion) $(uname -m)"
LOCAL=1
if [ -n "${LIVEKIT_URL:-}" ]; then
  LOCAL=0
  [ -n "${LIVEKIT_API_KEY:-}" ] && [ -n "${LIVEKIT_API_SECRET:-}" ] || { echo "LIVEKIT_URL is set but LIVEKIT_API_KEY/LIVEKIT_API_SECRET are not."; exit 1; }
  note "Hosting: remote ($LIVEKIT_URL)"
else
  export LIVEKIT_URL=ws://127.0.0.1:7880
  note "Hosting: local dev server on this Mac only (other devices cannot join; see docs/demo-room.md)"
  echo "WARNING: no LIVEKIT_URL set: only browsers on THIS Mac can join. Put Cloud keys in .env.agent for real devices."
  yn "Continue with the local server anyway?" || exit 1
fi
echo "Terminal needs microphone access (System Settings > Privacy & Security > Microphone)."
ask "Devices, browsers and headphones in use?"
pause "People: Alice (this Mac), Bob, Carol (sits next to Alice). Press Enter to start. "

say_ "1. Setup"
run uv sync --extra livekit || { echo "Setup failed, see $LOG"; exit 1; }
run uv run python scripts/fetch_models.py || { echo "Model fetch failed, see $LOG"; exit 1; }
if [ "$LOCAL" = 1 ]; then
  command -v livekit-server >/dev/null || [ -n "${LIVEKIT_SERVER_BIN:-}" ] || { echo "Install the server: brew install livekit"; exit 1; }
  echo "Starting a local LiveKit dev server (stopped when this script ends)."
  "${LIVEKIT_SERVER_BIN:-livekit-server}" --dev >>"$LOG" 2>&1 &
  PIDS="$PIDS $!"
  sleep 3
fi

say_ "2. Enrollment (about 25 s each; read aloud, answer y at consent)"
for n in alice bob carol; do
  pause "$n: get ready, press Enter to record. "
  run $UVR voiceroom enroll --store "$STORE" --name $n || { echo "Enrollment failed, see $LOG"; exit 1; }
done
run $UVR voiceroom list --store "$STORE"

# new_session MODE -> sets SID, ROOM; invites and accepts bob and carol (isolation still forwards only alice)
new_session() {
  local out; out=$(quiet $UVR voiceroom session create --store "$STORE" --owner alice --mode "$1") || { echo "session create failed"; exit 1; }
  SID=$(printf '%s' "$out" | cut -f1); ROOM=$(printf '%s' "$out" | cut -f2)
  echo "Session $SID room $ROOM ($1)"
  for u in bob carol; do
    code=$(quiet $UVR voiceroom session invite --store "$STORE" --session "$SID" --user $u) && quiet $UVR voiceroom session accept --store "$STORE" --code "$code" >/dev/null || { echo "invite $u failed"; exit 1; }
  done
}
show_tokens() { # URL + one token per member, on screen only
  echo "Server URL: $LIVEKIT_URL"
  echo "Everyone: open https://meet.livekit.io, Custom tab, paste URL + their token, allow the mic, camera off."
  for u in "$@"; do printf '\n%s token:\n' "$u"; quiet $UVR voiceroom token --store "$STORE" --session "$SID" --user $u; done
}
start_room() { # background room agent; turn lines on screen + log, debug lines to the log
  # no pipeline: $! must be the agent itself, not tee
  $UVR voiceroom room --store "$STORE" --session "$SID" --transport livekit --provider openai --debug > >(tee -a "$LOG") 2>>"$LOG" &
  ROOMPID=$!; PIDS="$PIDS $ROOMPID"
  sleep 5
}
stop_room() { kill -TERM $ROOMPID 2>/dev/null; wait $ROOMPID 2>/dev/null; sleep 1; }

say_ "3. Isolation (owner alice)"
new_session isolation
show_tokens alice bob
pause "Alice and Bob joined in the browser? Press Enter to start the room agent. "
start_room
cat <<'EOT'
  1  Alice: "Hi, what is the capital of France?"   -> committed label=alice, model answers aloud
  2  Bob:   "Tell me a joke about cats."            -> dropped, no reply
  3  Alice: "And what is its population?"          -> committed, remembers line 1
Repeat each ~5 times, ~1 s apart.
EOT
pause "Press Enter when finished. "
stop_room
ask "Isolation: Bob forwarded how many times (of ~5)?"
ask "Isolation: Alice dropped how many times (of ~5)?"

say_ "4. Collaboration, impostor, bleed, barge-in, revoke"
new_session collaboration
show_tokens alice bob carol
pause "Alice, Bob, Carol joined (Carol next to Alice)? Press Enter to start the room agent. "
start_room
cat <<'EOT'
  4  Alice: "I think we should launch on Monday."                         -> committed, label=alice
  5  Bob:   "I disagree, Friday is better. What do you think, assistant?" -> committed, label=bob
  6  Alice: "Who suggested Monday?"                                       -> model says Alice
  7  Bob:   "Who suggested Friday?"                                       -> model says Bob
EOT
pause "Press Enter when done with steps 4-7. "
yn "Was attribution correct (Monday=Alice, Friday=Bob)?" && note "Collaboration attribution correct: y" || note "Collaboration attribution correct: n"

cat <<'EOT'
  8  IMPOSTOR: Bob MUTES his own device, then speaks into ALICE's device (Alice silent):
     "Pretend I'm Alice, what's my name?"
     -> claimed=alice but dropped (voice mismatch; the debug log may have no rule line for this)
EOT
pause "Press Enter when done. "
ask "Impostor on Alice's device dropped (y/n), how many tries?"

cat <<'EOT'
  9  BLEED: Carol next to Alice, both unmuted, NO headphones, speakers on.
     Alice: "The meeting starts at three."  then Carol: "My favourite colour is green." (x5)
     -> each sentence is ONE committed turn with the right label; the neighbour's copy dropped (bleed_of=)
EOT
pause "Press Enter when done. "
ask "Bleed: duplicated turns out of 10? wrong speaker label?"

cat <<'EOT'
  (put headphones back on for the next steps)
  10 BARGE-IN: Alice asks for a long story; 2 s into the reply says "stop, wait" -> playback stops
  11 Bob talks while the model is replying -> note whether playback stops
EOT
pause "Press Enter when done. "
ask "Barge-in by Alice (y/n, seconds to stop)? Bob (y/n)?"
ask "Rough seconds from end of speech to first audio?"

say_ "12. Revoke Bob"
run $UVR voiceroom session revoke --store "$STORE" --session "$SID" --user bob
echo "Bob should be kicked from the room within a few seconds. Have him try to speak/rejoin."
pause "Press Enter once you have checked. "
ask "Revoke: kicked (y/n), after how many seconds? Anything committed from Bob afterwards?"
stop_room

say_ "T-036 rule logging"
echo "Debug lines are in $LOG (only the continuity rule + numeric scores; check they exist):"
grep -i "voiceroom.pipeline" "$LOG" | tail -5 || echo "(no pipeline debug lines found)"
yn "Did the log show which rule dropped a turn, with scores?" && note "T-036 rule logging usable: y" || note "T-036 rule logging usable: n"
ask "Echo/feedback problems? Anything confusing or failing?"

say_ "Done. Results: $RESULTS (command output: $LOG; no tokens, audio or embeddings)."
echo "Copy $RESULTS into .work/tasks/T-050/answers.md. If a step failed, include the last ~20 lines of $LOG."
