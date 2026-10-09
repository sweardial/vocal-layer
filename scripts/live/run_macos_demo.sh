#!/usr/bin/env bash
# Guided macOS live test (docs/demo-macos.md as one script). Run from anywhere on your Mac:
#   bash scripts/live/run_macos_demo.sh
# Reads OPENAI_API_KEY from the environment or from ./.env. Writes data/demo-results.txt
# (answers you give) and data/demo-log.txt (command output). Nothing audio-like is kept.
set -u
cd "$(dirname "$0")/../.." || exit 1
mkdir -p data
RESULTS=data/demo-results.txt
LOG=data/demo-log.txt
: >"$RESULTS"; : >"$LOG"
export PYTHONUNBUFFERED=1
export VOICEROOM_STORE=/tmp/vr-demo-store
trap ':' INT   # Ctrl-C stops the running demo, not this script

say_() { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '%s\n' "$*" | tee -a "$RESULTS" >/dev/null; }
pause() { read -r -p "${1:-Press Enter to continue}" _; }
yn() { local a; while true; do read -r -p "$1 [y/n] " a; case "$a" in y|Y) return 0;; n|N) return 1;; esac; done; }
ask() { local a; read -r -p "$1 " a; note "$1 $a"; }
run() { # run a command, show and log its output, remember failures
  printf '\n$ %s\n' "$*" | tee -a "$LOG"
  "$@" 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[0]}
  [ "$rc" -ne 0 ] && { echo "(exit code $rc)" | tee -a "$LOG"; note "FAILED (exit $rc): $*"; }
  return "$rc"
}

# ---- 0. prerequisites ------------------------------------------------------------------
say_ "0. Checking prerequisites"
[ "$(uname)" = Darwin ] || { echo "This is for macOS."; exit 1; }
if [ -f .env ]; then set -a; . ./.env; set +a; fi
command -v uv >/dev/null || { echo "Install uv first: brew install uv"; exit 1; }
[ -n "${OPENAI_API_KEY:-}" ] || { echo "OPENAI_API_KEY not set. Put OPENAI_API_KEY=sk-... in .env in the repo root, or export it."; exit 1; }
note "Environment: $(sw_vers -productVersion) $(uname -m), $(uv run python -V 2>&1)"
echo "Microphone access: your terminal app needs it (System Settings > Privacy & Security > Microphone)."
echo "Use headphones for Alice if you can (laptop speakers cause echo)."
ask "Headphones or speakers? Which mic?"
TWO=0; yn "Is a second person (Bob) with you for the isolation and collaboration steps?" && TWO=1
note "Second person present: $TWO"
pause "Quiet room? Press Enter to start. "

# ---- 1. setup --------------------------------------------------------------------------
say_ "1. Setup"
run uv sync || { echo "Setup failed, see $LOG"; exit 1; }
run uv run python scripts/fetch_models.py || { echo "Model fetch failed, see $LOG"; exit 1; }
ls models/ | tee -a "$LOG"
run uv run voiceroom --help >/dev/null || exit 1
note "Setup OK"

# ---- 2. raw OpenAI path check (cheap, validates key and network) ------------------------
say_ "2. Raw OpenAI check (no mic needed)"
run say -o data/hello.wav --data-format=LEI16@16000 "Hello, can you hear me? Please say a short greeting."
run uv run python scripts/live/openai_realtime_check.py --wav data/hello.wav --label alice
if [ -f data/out/openai_reply.wav ]; then
  echo "Playing the reply..."; afplay data/out/openai_reply.wav
  yn "Was the reply intelligible speech?" && note "OpenAI reply intelligible: y" || note "OpenAI reply intelligible: n"
else
  note "OpenAI check produced no reply file"
  yn "The OpenAI check failed. Continue anyway?" || { echo "Stopped. Results: $RESULTS"; exit 1; }
fi
grep -h "first audio after" "$LOG" | tail -1 | tee -a "$RESULTS"

# ---- 3. enrollment ---------------------------------------------------------------------
say_ "3. Enrollment: Alice, then Bob (about 25 s each). Speak naturally the whole time; answer y at the consent prompt."
pause "Alice, get ready to read a paragraph aloud. Press Enter to record. "
run uv run voiceroom enroll --name alice
if [ "$TWO" = 1 ]; then
  pause "Now Bob. Press Enter to record. "
  run uv run voiceroom enroll --name bob
fi
run uv run voiceroom list
yn "Does the list show the expected names?" && note "Enroll + list OK: y" || note "Enroll + list OK: n"

# ---- 4. isolation ----------------------------------------------------------------------
if [ "$TWO" = 1 ]; then
  say_ "4. Isolation demo (live OpenAI). Leave about 1 s between lines. Alice wears headphones."
  cat <<'EOF'
  1  Alice: "Hi, what is the capital of France?"      -> forwarded, model answers aloud
  2  Bob:   "Tell me a joke about cats."               -> dropped, no reply
  3  Alice: "And what is its population?"              -> forwarded, remembers line 1
  4  Alice: ask for a long story; 2 s into the reply say "stop, wait" -> playback stops
  5  Bob:   talk while the model is replying           -> playback NOT interrupted
Do each line about 5 times if you can. Press Ctrl-C when finished.
EOF
  pause "Press Enter to start the demo. "
  run uv run voiceroom demo --mode isolation --owner alice --provider openai
  echo; echo "Answer from what you saw in the log lines above (speaker, forwarded/dropped)."
  ask "Isolation: how many times was Bob forwarded (out of ~5)?"
  ask "Isolation: how many times was Alice dropped (out of ~5)?"
  ask "Barge-in: did Alice interrupt playback (y/n) and after how many seconds?"
  ask "Barge-in: did Bob interrupt playback (y/n)?"
  ask "Rough seconds from end of speech to first audio?"
  ask "Echo or feedback problems? Did headphones fix them?"

  # ---- 5. collaboration ----------------------------------------------------------------
  say_ "5. Collaboration demo (live OpenAI)"
  cat <<'EOF'
  1  Alice: "I think we should launch on Monday."
  2  Bob:   "I disagree, Friday is better. What do you think, assistant?"
  3  Alice: "Who suggested Monday?"   -> model should say Alice
  4  Bob:   "Who suggested Friday?"   -> model should say Bob
Press Ctrl-C when finished.
EOF
  pause "Press Enter to start the demo. "
  run uv run voiceroom demo --mode collaboration --provider openai
  yn "Was speaker attribution correct (steps 3 and 4)?" && note "Collaboration attribution correct: y" || note "Collaboration attribution correct: n"
else
  say_ "Skipping isolation and collaboration (needs a second person). They stay unverified."
  note "Isolation, barge-in and collaboration: NOT TESTED (no second person)"
fi

# ---- 6. cleanup and report -------------------------------------------------------------
say_ "6. Cleanup"
run uv run voiceroom delete --name alice
[ "$TWO" = 1 ] && run uv run voiceroom delete --name bob
rm -rf /tmp/vr-demo-store data/hello.wav data/out/openai_reply.wav
ask "Anything confusing or any other failures?"

say_ "Done. Results are in $RESULTS (command output in $LOG)."
echo "Paste $RESULTS into .work/tasks/T-023/answers.md (or the Telegram reply to the T-023 message)."
echo "If a step failed, send the last ~20 lines of $LOG too."
