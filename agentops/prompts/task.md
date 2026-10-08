You are working unattended on task {id}: {title} (milestone {ms}). No human is watching, so never ask questions in chat.

Backlog entry:
{body}

Testing policy (.work/POLICY.md):
{policy}

Notes from the human (.work/inbox.md). These take priority:
{inbox}

Answers from the human for this task:
{answers}

Process:
1. Read CLAUDE.md, the relevant parts of SPEC.md, .work/decisions.md, and .work/tasks/{id}/ (it may hold earlier work on this task).
2. Non-trivial task: have the `planner` subagent write .work/tasks/{id}/plan.md. Trivial task: write a 5-line plan yourself.
3. Implement. Prove it works exactly as .work/POLICY.md says (don't write unit tests unless it says they're on). Run `make check` until green.
4. Have the `reviewer` subagent review task {id} (it has a fresh context). Fix every HIGH and MEDIUM finding, then re-run `make check`.
5. Write .work/tasks/{id}/summary.md: what changed, how it was verified (commands + results), follow-ups.
   Add follow-up tasks to the end of .work/backlog.md (new IDs, same format) only if truly needed.
6. Do NOT change task status boxes in backlog.md and do NOT git commit. The harness does that.
   An independent reviewer using a different model checks your work after you finish.

If you're blocked by something only a human can provide: do everything possible without it first (mocks, stubs, docs).
Then write .work/tasks/{id}/needs_human.md (one concise question, options, and your recommendation) and stop.

Final message: at most 3 lines. Say what was done, the verification result, and anything risky.
