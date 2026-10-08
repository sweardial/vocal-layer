#!/bin/bash
set -e
git config --global user.name "${GIT_AUTHOR_NAME:-cook-agent}"
git config --global user.email "${GIT_AUTHOR_EMAIL:-cook-agent@localhost}"
git config --global --add safe.directory /workspace

if [ -z "$CLAUDE_CODE_OAUTH_TOKEN" ] && [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "ERROR: set CLAUDE_CODE_OAUTH_TOKEN (run 'claude setup-token' on your Mac) in agentops/.env.agent" >&2
  exit 1
fi

# Codex (cross-model reviewer): API-key login if not already logged in (e.g. via `./cook shell` → `codex login`)
if command -v codex >/dev/null && [ ! -s "$HOME/.codex/auth.json" ] && [ -n "$OPENAI_API_KEY" ]; then
  printenv OPENAI_API_KEY | codex login --with-api-key >/dev/null 2>&1 \
    || echo "WARN: codex login failed; cross-model review will be skipped" >&2
fi

exec "$@"
