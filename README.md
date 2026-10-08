# Voice Room

Speaker-aware layer for realtime voice AI. See [SPEC.md](SPEC.md).

This repo is built by an unattended agent loop (`agentops/`). You only get involved at gates.

## Run it
```bash
./cook setup        # once: Claude token, OpenAI key (optional), Telegram bot
./cook codex-login  # once: Codex reviewer via your ChatGPT account
./cook           # start in Docker and walk away
./cook status    # or /status in Telegram
./cook logs | stop | shell | rebuild
```

## The loop
1. **Plan** (Opus) writes `.work/backlog.md`. You get it in Telegram → `/approve` or reply with feedback.
2. **Per task:** a Sonnet session plans (planner subagent), implements, proves it runs, and gets a fresh-context review (reviewer subagent).
   Then the harness runs `make check` and a **Codex** cross-model review. Failures go back for up to 3 fix rounds. Then commit and notify.
3. **Blocked?** The agent writes `needs_human.md`, you get a Telegram question, and it moves on to other tasks. Your reply unblocks it.
4. **Usage limit?** It sleeps until the reset time and resumes the same session.

## Telegram commands
`/status` `/pause` `/resume` `/approve` `/plan <next goal>` `/tests on|off` `/help`. Any other text answers a blocked task
(`T-007: …` if several are blocked) or becomes a note in `.work/inbox.md` that every agent reads.

## Testing policy
Unit tests are **off** until you send `/tests on`. Until then agents prove changes with a few disposable smoke scripts (`scripts/smoke/`, run by `make check`). The current policy is in `.work/POLICY.md`.

## Knobs
`agentops/config.json`: milestones in scope, models, fix rounds, timeouts. Prompts live in `agentops/prompts/`, roles in `.claude/agents/`.
