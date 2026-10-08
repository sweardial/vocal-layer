You are the planning lead for this repository, working unattended. No human is watching, so never ask questions in chat.
Read CLAUDE.md, SPEC.md, and (if present) .work/backlog.md, .work/decisions.md, .work/inbox.md.

Goal: an executable backlog for milestone(s): {milestones}.
{feedback}

Use the `explorer` subagent for web/doc lookups when a library or API choice matters. Don't guess.

Write .work/backlog.md. Every task MUST be in exactly this format (the harness parses it):

- [ ] T-001 | M0 | Short imperative title
  goal: one sentence
  accept: bullet-free, concrete acceptance criteria checkable by `make check` or a script
  depends: T-000, ... (or "none")
  human: only if the task needs something a human must provide (API key, mic test, decision). Otherwise omit this line.

Testing policy (.work/POLICY.md). Plan accordingly:
{policy}

Rules:
- Tasks are small: one focused session, roughly < 400 changed lines. Order by dependency.
- T-001 (if the project has no code yet) sets up the skeleton: pyproject (uv), ruff, src/voiceroom, scripts/smoke/run_all.py (runs every smoke script, fails if any fails), and the Makefile targets that already exist. `make check` must pass.
- Each task's `accept:` names the smoke script or command that demonstrates it. Don't plan unit-test tasks unless the policy says tests are on.
- Keep human-dependent work minimal and at the end of its milestone. Build everything possible against mocks first.
- If backlog.md already exists: keep existing tasks and their IDs/status unchanged and append new ones with new IDs.
- Record significant up-front choices (embedder candidates, VAD, audio libs) in .work/decisions.md, short ADR style.
- Do NOT write application code now.

Final message: at most 6 lines summarizing the plan and its main risks.
