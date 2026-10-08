FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates make build-essential jq ffmpeg libsndfile1 libportaudio2 \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code @openai/codex
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Claude Code refuses --dangerously-skip-permissions as root
RUN useradd -m -s /bin/bash agent
USER agent
RUN mkdir -p /home/agent/.claude /home/agent/.codex /home/agent/.cache

# Keep the Linux venv out of the bind-mounted repo so it never clashes with a macOS venv
ENV UV_PROJECT_ENVIRONMENT=/home/agent/.venv-voiceroom \
    UV_LINK_MODE=copy \
    DISABLE_AUTOUPDATER=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace
ENTRYPOINT ["entrypoint.sh"]
CMD ["python3", "agentops/cook.py"]
