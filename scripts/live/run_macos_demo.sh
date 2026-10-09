#!/usr/bin/env bash
# Guided macOS live test (docs/demo-macos.md as one script). Run from anywhere on your Mac:
#   bash scripts/live/run_macos_demo.sh              # full run
#   bash scripts/live/run_macos_demo.sh --demo-only  # skip setup, OpenAI check and enrollment
# Reads OPENAI_API_KEY from the environment or from ./.env. Writes data/demo-results.txt
# (answers you give) and data/demo-log.txt (command output). Nothing audio-like is kept.
set -u
cd "$(dirname "$0")/../.." || exit 1
# Load configuration first so nothing in .env can redirect the isolated demo store chosen below.
# VOICEROOM_ENV_FILE / VOICEROOM_DEMO_OUT / VOICEROOM_DEMO_STORE exist for the offline smoke test.
ENV_FILE="${VOICEROOM_ENV_FILE:-.env}"
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi
OUT="${VOICEROOM_DEMO_OUT:-data}"
mkdir -p "$OUT" data
RESULTS=$OUT/demo-results.txt
LOG=$OUT/demo-log.txt
if [ "${1:-}" = "--demo-only" ]; then :; else : >"$RESULTS"; : >"$LOG"; fi
export PYTHONUNBUFFERED=1
# The demo store is a private mktemp dir created by this script. --demo-only resumes the store
# recorded by a previous run, after checking it is a real (non-symlink), user-owned vr-demo.* dir.
STORE_REC="$OUT/demo-store.txt"
if [ "${1:-}" = "--demo-only" ]; then
  p=$(cat "$STORE_REC" 2>/dev/null) || { echo "No previous run recorded in $STORE_REC; run without --demo-only first."; exit 1; }
  case "$p" in */../*|*/..|*/./*|"") echo "Refusing suspicious demo store path: $p"; exit 1;; esac
  case "$(basename "$p")" in vr-demo.*) ;; *) echo "Refusing demo store with unexpected name: $p"; exit 1;; esac
  { [ -d "$p" ] && [ ! -L "$p" ] && [ -O "$p" ]; } || { echo "Refusing demo store (missing, symlink or not yours): $p"; exit 1; }
  STORE=$(cd -P "$p" && pwd -P) || exit 1
else
  STORE=$(mktemp -d "${TMPDIR:-/tmp}/vr-demo.XXXXXX") || exit 1
  STORE=$(cd -P "$STORE" && pwd -P)
  printf '%s\n' "$STORE" >"$STORE_REC"
fi
unset VOICEROOM_STORE   # every voiceroom subcommand below passes --store "$STORE" explicitly
UVR="uv run"

say_() { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '%s\n' "$*" | tee -a "$RESULTS" >/dev/null; }
pause() { read -r -p "${1:-Press Enter to continue}" _; }
yn() { local a; while true; do read -r -p "$1 [y/n] " a; case "$a" in y|Y) return 0;; n|N) return 1;; esac; done; }
ask() { local a; read -r -p "$1 " a; note "$1 $a"; }
run() { # run a command, show and log its output, remember failures
  printf '\n$ %s\n' "$*" | tee -a "$LOG"
  trap ':' INT   # Ctrl-C ends the running command (e.g. a demo), not this script
  "$@" 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[0]}
  trap - INT
  [ "$rc" -ne 0 ] && { echo "(exit code $rc)" | tee -a "$LOG"; note "FAILED (exit $rc): $*"; }
  return "$rc"
}

# ---- 0. prerequisites ------------------------------------------------------------------
say_ "0. Checking prerequisites"
[ "$(uname)" = Darwin ] || { echo "This is for macOS."; exit 1; }
command -v uv >/dev/null || { echo "Install uv first: brew install uv"; exit 1; }
[ -n "${OPENAI_API_KEY:-}" ] || { echo "OPENAI_API_KEY not set. Put OPENAI_API_KEY=sk-... in .env in the repo root, or export it."; exit 1; }
note "Environment: $(sw_vers -productVersion) $(uname -m), $(uv run python -V 2>&1)"
echo "Microphone access: your terminal app needs it (System Settings > Privacy & Security > Microphone)."
echo "Use headphones for Alice if you can (laptop speakers cause echo)."
ask "Headphones or speakers? Which mic?"
TWO=0; yn "Is a second person (Bob) with you for the isolation and collaboration steps?" && TWO=1
note "Second person present: $TWO"
pause "Quiet room? Press Enter to start. "

DEMO_ONLY=0; [ "${1:-}" = "--demo-only" ] && DEMO_ONLY=1   # skip setup, OpenAI check and enrollment (reuse the stored voices)
if [ "$DEMO_ONLY" = 0 ]; then
# ---- 1. setup --------------------------------------------------------------------------
say_ "1. Setup"
run uv sync || { echo "Setup failed, see $LOG"; exit 1; }
run uv run python scripts/fetch_models.py || { echo "Model fetch failed, see $LOG"; exit 1; }
ls models/ | tee -a "$LOG"
run $UVR voiceroom --help >/dev/null || exit 1
note "Setup OK"

# ---- 2. raw OpenAI path check (cheap, validates key and network) ------------------------
say_ "2. Raw OpenAI check (no mic needed)"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/vr-demo-audio.XXXXXX") || exit 1   # fresh dir: never overwrites earlier audio
WAV="$WORK/hello.wav"; REPLY="$WORK/reply.wav"
run say -o "$WAV" --data-format=LEI16@16000 "Hello, can you hear me? Please say a short greeting."
if run uv run python scripts/live/openai_realtime_check.py --wav "$WAV" --label alice --out "$REPLY" && [ -s "$REPLY" ]; then
  echo "Playing the reply..."; afplay "$REPLY"
  yn "Was the reply intelligible speech?" && note "OpenAI reply intelligible: y" || note "OpenAI reply intelligible: n"
else
  note "OpenAI check FAILED or produced no reply"
  yn "The OpenAI check failed. Continue anyway?" || { rm -rf "$WORK"; echo "Stopped. Results: $RESULTS"; exit 1; }
fi
grep -h "first audio after" "$LOG" | tail -1 | tee -a "$RESULTS"

# ---- 3. enrollment ---------------------------------------------------------------------
say_ "3. Enrollment: Alice, then Bob (about 25 s each). Speak naturally the whole time; answer y at the consent prompt."
read_aloud() {
  cat <<'EOF'

  READ THIS ALOUD at a natural pace, then keep reading or talking until recording stops (~25 s):

  "The old lighthouse stood at the edge of the harbor, its white paint worn thin by decades of
  salt and wind. Every morning the fishermen set out before sunrise, counting on the steady
  light to guide them home. On quiet evenings the keeper would climb the narrow stairs, trim
  the lamp, and watch the boats return one by one. He often said that patience was the real
  trade of the sea: waiting for the tide, waiting for the weather, and waiting for the
  familiar sound of an engine coming across the dark water."

EOF
}
read_aloud
pause "Alice, get ready to read the text above. Press Enter to record. "
run $UVR voiceroom enroll --store "$STORE" --name alice || { echo "Enrollment failed, see $LOG"; exit 1; }
if [ "$TWO" = 1 ]; then
  read_aloud
  pause "Now Bob: read the text above. Press Enter to record. "
  run $UVR voiceroom enroll --store "$STORE" --name bob || { echo "Enrollment failed, see $LOG"; exit 1; }
fi
run $UVR voiceroom list --store "$STORE"
yn "Does the list show the expected names?" && note "Enroll + list OK: y" || note "Enroll + list OK: n"
fi

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
  run $UVR voiceroom demo --store "$STORE" --mode isolation --owner alice --provider openai
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
  run $UVR voiceroom demo --store "$STORE" --mode collaboration --provider openai
  yn "Was speaker attribution correct (steps 3 and 4)?" && note "Collaboration attribution correct: y" || note "Collaboration attribution correct: n"
else
  say_ "Skipping isolation and collaboration (needs a second person). They stay unverified."
  note "Isolation, barge-in and collaboration: NOT TESTED (no second person)"
fi

# ---- 6. cleanup and report -------------------------------------------------------------
say_ "6. Cleanup"
run $UVR voiceroom delete --store "$STORE" --name alice
[ "$TWO" = 1 ] && run $UVR voiceroom delete --store "$STORE" --name bob
rm -rf "$STORE" "$STORE_REC"
[ -n "${WORK:-}" ] && rm -rf "$WORK"   # run-owned mktemp dir with the test audio
ask "Anything confusing or any other failures?"

say_ "Done. Results are in $RESULTS (command output in $LOG)."
echo "Paste $RESULTS into .work/tasks/T-023/answers.md (or the Telegram reply to the T-023 message)."
echo "If a step failed, send the last ~20 lines of $LOG too."
