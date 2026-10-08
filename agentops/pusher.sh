#!/usr/bin/env bash
# Runs on the Mac, not in the container: pushes the agent's milestone branches to GitHub
# with your own git credentials, so the agent never has access to them.
cd "$(dirname "$0")/.."
mkdir -p .work/logs
export GIT_TERMINAL_PROMPT=0
while true; do
  if git for-each-ref --format='%(refname)' refs/heads/milestone/ | grep -q .; then
    out=$(git push -q origin 'refs/heads/milestone/*:refs/heads/milestone/*' 2>&1) \
      || echo "$(date '+%F %T') push failed: $out" >> .work/logs/pusher.log
  fi
  sleep 120
done
