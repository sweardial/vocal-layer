# Handoff: read this first in any interactive session

You are in an **interactive** session with the project owner (a software engineer). This repo is also built
**unattended** by agents running in Docker. This file explains how that system works, what's been done,
and how to work alongside it without breaking it. Build agents don't need this file; it's for you.

Live progress is always in `.work/STATUS.md`, or `./cook status`. This file covers how things work, not the live state.

## The product (details in SPEC.md)

**Voice Room** is a speaker-aware layer between audio and realtime voice models (OpenAI Realtime first,
provider-agnostic). Users enroll a voice signature (an embedding).
- **Isolation mode:** the model only hears the owner.
- **Collaboration mode:** every enrolled speaker is forwarded, with attribution (`[Speaker: Alice]`).

Later: multi-mic rooms and invites (LiveKit), a public API, and overlapping speech.

Core design choice: we disable the provider's own turn detection and commit audio turns ourselves, only for accepted speakers.
Choices so far (`.work/decisions.md`, D-001 to D-007):
- **Embedders:** WeSpeaker ResNet34 run as ONNX is the primary; SpeechBrain ECAPA is optional and comparison-only.
- **VAD:** Silero ONNX, with an energy-based fallback.
- **Signature store:** Fernet-encrypted.
- **Eval data:** LibriSpeech plus synthetic conversations.

Milestones: **M0** = speaker-ID core + benchmark (T-001 to T-012). **M1** = live isolation/collaboration with the Realtime API (T-013 to T-023).

## The owner's preferences (follow them)

- **Minimal involvement.** The owner wants to start things and leave. Ask them only when work truly can't continue.
- **No unit tests until they turn them on** (`/tests on`). Tests would lock in implementations that are still changing.
  Until then, the proof of work is a few disposable smoke scripts in `scripts/smoke/`, run by `make check`.
- **Telegram stays quiet.** Only blockers, plan approvals, and milestone-ready-for-review messages. No progress chatter.
- **Codex runs on their ChatGPT login, not an API key** (API usage is too expensive).
- **Prefer doing over asking.** They get frustrated by repeated manual setup steps. Fix things yourself when you can.

## How the unattended system works

```
./cook (host script) ──docker run──▶ container voice-room-cook (python 3.12, claude, codex, uv)
                                      └─ agentops/cook.py  (the harness, PID 1)
                                           plan (Opus) → owner /approve in Telegram
                                           per task:  claude -p (Sonnet) + planner/reviewer/explorer subagents
                                                      → make check → Codex review (cross-model) → ≤3 fix rounds
                                                      → git commit "T-xxx: …" on milestone/<ms>
                                           usage limit → sleep until reset, resume same session
                                           blocked → .work/tasks/T-xxx/needs_human.md → Telegram → owner replies
host: agentops/pusher.sh pushes milestone/* to GitHub every 2 min (the container has no git credentials)
host: caffeinate keeps the Mac awake while running
```

Key files:
| Path | What |
|---|---|
| `agentops/cook.py` | Harness: loop, Telegram, usage-limit waits, verification, Codex review, commits, branches, Q&A |
| `agentops/config.json` | Knobs: milestones in scope, models, fix rounds, timeouts, disallowed tools, `notify_progress` |
| `agentops/prompts/*.md` | Planner / task / Codex-review prompts |
| `.claude/agents/*.md` | Subagents: `planner` (Opus), `reviewer` (Opus, fresh context), `explorer` (Haiku, lookups) |
| `agentops/Dockerfile`, `entrypoint.sh` | Image. The agent runs as non-root user `agent`; its home is Docker volume `voice-room-cook-home` (Claude sessions, Codex login, uv venv) |
| `agentops/.env.agent` | Secrets: `CLAUDE_CODE_OAUTH_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, optional `OPENAI_API_KEY`. Gitignored, chmod 600. **Never print or commit it.** |
| `.work/backlog.md` | Tasks in a strict parsed format: `- [ ] T-001 \| M0 \| Title` + indented `goal/accept/depends/human` lines. Status: ` ` todo, `~` in progress, `x` done, `!` blocked |
| `.work/state.json` | Harness state: phase, per-task Claude session ids, base commits, Telegram offset. Gitignored |
| `.work/inbox.md` | Owner's notes, which every build agent reads before a task |
| `.work/POLICY.md` | Testing policy, written by the harness from `/tests on/off` |
| `.work/qa.json` | Telegram Q&A session id |
| `.work/logs/` | Raw Claude JSON per run, `pusher.log` |

Commands on the Mac:
```
./cook               start (builds the image if needed), plus the pusher and caffeinate
./cook stop          stop. All state is kept; ./cook resumes, including mid-task (session ids are saved up front)
./cook status        progress            ./cook logs    harness log (task start/done, checks, reviews)
./cook watch         live tool calls and messages of the agent and its subagents
./cook shell         bash in the container          ./cook rebuild   rebuild the image
./cook setup         re-enter tokens                ./cook codex-login   ChatGPT device login for Codex
```

Telegram (bot @ai_voicelayer_bot):
- A plain message goes to a read-only Q&A agent that sees the build state. It answers, or emits a directive:
  `@@NOTE:` (to `inbox.md`), `@@ANSWER T-xxx:` (unblocks a task), or `@@PLAN:` (revises a plan awaiting approval).
- Replying to a ⛔ blocker message passes your answer straight to that task.
- Commands: `/status /pause /resume /approve /plan <goal> /tests on|off /note <text> /new /help`.

## Git / GitHub workflow

- Remote: `https://github.com/sweardial/vocal-layer` (`origin`). Pushes come only from the host pusher, or from the owner.
- `main` = reviewed. Product work lands on `milestone/m0`, then `milestone/m1` (stacked), one commit per task.
- The owner merges pull requests whenever they like, **with merge commits (not squash)**. After a merge they can open a new pull request
  from the same branch, and it shows only the new commits. PR #1 (T-001 to T-004) is already merged into `main`.
- When a milestone finishes, the harness sends a Telegram message with the compare/PR link.
- **Harness changes are never part of task commits.** `stage()` excludes `agentops/` and `./cook`. If you change
  the harness, commit it yourself as a separate `harness: …` commit: `git commit -- agentops/<files>`.
  Don't commit anything else; product files belong to the running task.

## Working alongside the running agent (important)

- **Don't edit product code (`src/`, `scripts/`, `pyproject.toml`) while a task is running.** The agent owns the
  working tree and the harness commits everything. To change direction, add a note (`.work/inbox.md`, or Telegram),
  or `/pause` first.
- **Adding work:** append tasks to `.work/backlog.md` in the exact format with new IDs, or send `/plan <goal>`.
- **Changing the harness:** edit `agentops/cook.py`. It reloads itself between tasks, so no restart is needed. Test changes
  offline first: copy the repo to a scratch dir and use a fake `claude` script on PATH (that's how every harness change so far was tested).
- **Answering a blocker yourself:** write the answer to `.work/tasks/T-xxx/answers.md` and set the task's box back to `[ ]`.
- **Questions only** (owner on Remote Control, learning): just read. `SPEC.md`, `.work/decisions.md`, `.work/tasks/*/summary.md`
  and `git log -p` explain what was built and why.

## Lessons learned (don't repeat these)

- **Interactive-only tools hang headless runs.** The planner once called `ScheduleWakeup` and `claude -p` idled for 20 min.
  These tools are blocked via `disallowed_tools` in config.json. Keep that list when adding Claude runs.
- **Auto mode blocks `git push` from Claude sessions** (classified as data exfiltration). That's why the host pusher exists.
  Ask the owner to push, or rely on the pusher. Don't try to work around the block.
- **The harness used to sweep harness edits into task commits** (`git add -A`). Fixed by `stage()`. Keep it that way.
- **On the host, the Bash tool runs zsh:** `$VAR` with spaces doesn't word-split, so write path lists out literally.
  A bad pathspec once silently made a verification check meaningless.
- **Python on the Mac:** the default `python3` is uv-managed 3.14 (`~/.local/bin`, prepended in `~/.zshrc`). The old python.org 3.9/3.10/3.11
  installs are still present and have a broken CA bundle (`CERTIFICATE_VERIFY_FAILED`). The project itself is 3.12 in Docker.
- **Telegram long-polls occasionally get connection resets.** Both setup.py and cook.py retry or ignore them.

## Open items

- **Rotate tokens:** the Claude OAuth token and the Telegram bot token were pasted into a chat on 2026-10-08.
  Rotate with `claude setup-token` and BotFather `/revoke`, then update `agentops/.env.agent`.
- **M1 live demo** (T-019 to T-023) needs an `OPENAI_API_KEY` with a spending limit in `.env.agent`, and the owner's mic test on macOS.
- **T-012** (choosing the embedder and threshold) may come back as a decision for the owner.
- **Later:** turn unit tests on once M0/M1 stabilise. Plan M2+ via `/plan`. Consider a small cloud VM so building
  doesn't depend on the laptop being open.

## History

- **2026-10-08, setup session:** designed the product approach and the unattended pipeline, wrote SPEC/CLAUDE.md, the harness,
  Docker image, Telegram bot (notifications, then commands, then conversational Q&A), the no-unit-tests policy, Codex via ChatGPT login,
  the GitHub branch flow with the host pusher, and resumable sessions with self-reload. The plan (23 tasks) was approved, and T-001 to T-006
  were built the same day for an estimated ~$2.70 API-equivalent (on the subscription).
