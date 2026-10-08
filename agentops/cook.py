#!/usr/bin/env python3
"""Unattended dev loop.

plan (Opus) -> human approves via Telegram -> for each task:
  implement (Sonnet session, with planner/reviewer subagents) -> `make check`
  -> independent Codex review -> fix rounds -> commit -> notify.
Usage limits: sleep until reset, then resume the same Claude session.
All state lives in .work/ so the container can be restarted at any time.
"""
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OPS = ROOT / "agentops"
WORK = ROOT / ".work"
LOGS = WORK / "logs"
BACKLOG = WORK / "backlog.md"
STATE_F = WORK / "state.json"
CFG = json.loads((OPS / "config.json").read_text())

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

TASK_RE = re.compile(r"^- \[([ ~x!])\] (T-\d+) \| (M\d+) \| (.+)$")
LIMIT_RE = re.compile(r"usage limit|rate.?limit|limit reached|limit will reset|out of (extra )?usage|\b429\b|overloaded|quota", re.I)
ICON = {" ": "⬜", "~": "🔨", "x": "✅", "!": "⛔"}

POLICY_OFF = """Unit tests: OFF (the human decides when to turn them on; the product is still exploratory and tests
would lock in implementations that are likely to change).
- Do NOT write unit tests, pytest files, or test fixtures. Don't plan or request them in reviews.
- Prove each change works with a coarse runnable check: add or update a smoke script in scripts/smoke/
  (one per feature, e.g. run the pipeline on a synthetic conversation and assert the outcome). `make smoke` runs them all.
- Keep smoke scripts few, fast (< 60 s total), offline, and freely rewritable when the design changes."""
POLICY_ON = """Unit tests: ON (enabled by the human).
- Add focused pytest tests for behavior that has stabilized, plus integration tests in tests/integration/ for
  end-to-end flows. Don't test implementation details that are still in flux.
- Keep the smoke scripts in scripts/smoke/. `make check test` must pass."""


def now():
    return datetime.datetime.now()


def log(msg):
    print(f"[{now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- state / git

def load_state():
    if STATE_F.exists():
        return json.loads(STATE_F.read_text())
    return {"phase": "plan", "sessions": {}, "bases": {}, "telegram_offset": 0,
            "paused": False, "cost_usd": 0.0, "feedback": "", "current": None}


def save_state(s):
    STATE_F.write_text(json.dumps(s, indent=2))


def tests_on(state):
    return state.get("unit_tests", CFG.get("unit_tests", False))


def policy(state):
    return POLICY_ON if tests_on(state) else POLICY_OFF


def write_policy(state):
    (WORK / "POLICY.md").write_text("# Testing policy (set by the human, managed by the harness)\n\n" + policy(state) + "\n")


def git(*args, check=False):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=check)
    return r.stdout.strip()


def branch_for(ms):
    return f"milestone/{ms.lower()}"


def ensure_branch(ms):
    """Each milestone's tasks land on their own branch (stacked on the previous one) → one reviewable PR per milestone."""
    want = branch_for(ms)
    if git("rev-parse", "--abbrev-ref", "HEAD") == want:
        return
    if git("rev-parse", "--verify", "-q", want):
        git("checkout", "-q", want)
    else:
        git("checkout", "-q", "-b", want)
    log(f"on branch {want}")


def review_url(ms):
    url = git("remote", "get-url", "origin")
    if not url:
        return ""
    url = url.removesuffix(".git").replace("git@github.com:", "https://github.com/")
    order = CFG["milestones"]
    i = order.index(ms) if ms in order else 0
    base = "main" if i == 0 else branch_for(order[i - 1])
    return f"{url}/compare/{base}...{branch_for(ms)}?expand=1"


def stage():
    """Stage the agents' work. Harness files (agentops/, ./cook) are never part of a task commit."""
    git("add", "-A", "--", ".", ":!agentops", ":!cook")


def commit(message):
    stage()
    if git("status", "--porcelain"):
        git("commit", "-q", "-m", message + "\n\nCo-Authored-By: Claude <noreply@anthropic.com>")


# ---------------------------------------------------------------- telegram

def tg(method, wait=0, **params):
    if not TG_TOKEN:
        return None
    data = urllib.parse.urlencode(params).encode()
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}"
        with urllib.request.urlopen(url, data, timeout=wait + 15) as r:
            return json.loads(r.read())
    except Exception as e:  # network blips must never kill the loop
        log(f"telegram {method} failed: {e}")
        time.sleep(min(wait, 5))
        return None


def notify(text, reply_to=None, progress=False):
    """Send to the owner's chat; returns the Telegram message ids sent.
    progress=True messages (task started/done, waiting on limits) only go to the log unless notify_progress is on."""
    log(("PROGRESS " if progress else "NOTIFY ") + text.replace("\n", " | ")[:300])
    ids = []
    if progress and not CFG.get("notify_progress", False):
        return ids
    if TG_CHAT:
        for i in range(0, len(text), 3900):
            extra = {"reply_to_message_id": reply_to} if reply_to and i == 0 else {}
            r = tg("sendMessage", chat_id=TG_CHAT, text=text[i:i + 3900], **extra)
            if r and r.get("ok"):
                ids.append(r["result"]["message_id"])
    return ids


def poll(state, timeout=0):
    """Fetch new messages from the owner's chat; long-polls up to `timeout` seconds."""
    if not TG_TOKEN:
        time.sleep(timeout)
        return []
    r = tg("getUpdates", wait=timeout, offset=state["telegram_offset"], timeout=timeout)
    msgs = []
    for u in (r or {}).get("result", []):
        state["telegram_offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) == TG_CHAT and m.get("text"):
            msgs.append((m["text"].strip(), m["message_id"], (m.get("reply_to_message") or {}).get("message_id")))
    save_state(state)
    return msgs


def handle(state, msgs):
    for msg in msgs:
        text, msg_id, reply_to = msg if isinstance(msg, tuple) else (msg, None, None)
        cmd, _, rest = text.partition(" ")
        cmd = cmd.lower().split("@")[0]
        if cmd == "/ask" or (reply_to and reply_to in qa_load().get("msg_ids", [])):
            question = rest if cmd == "/ask" else text
            if question.strip():
                threading.Thread(target=qa_answer, args=(question, msg_id), daemon=True).start()
            else:
                notify("Usage: /ask <question>. Reply to my answer to follow up, /new for a fresh topic.")
        elif cmd == "/new":
            qa_save({"session": None, "msg_ids": []})
            notify("🧹 New Q&A conversation. Ask away with /ask.")
        elif cmd == "/note" and rest.strip():
            with open(WORK / "inbox.md", "a") as f:
                f.write(f"\n- ({now():%Y-%m-%d %H:%M}) {rest.strip()}\n")
            notify("🗒 Added to inbox.md; agents will read it.")
        elif cmd in ("/status", "/start"):
            notify(status_text(state))
        elif cmd == "/pause":
            state["paused"] = True
            notify("⏸ Paused after the current step. /resume to continue.")
        elif cmd == "/resume":
            state["paused"] = False
            notify("▶️ Resumed.")
        elif cmd == "/approve" and state["phase"] == "await_approval":
            state["phase"] = "build"
            notify("✅ Plan approved. Starting work.")
        elif cmd == "/tests" and rest.strip().lower() in ("on", "off"):
            state["unit_tests"] = rest.strip().lower() == "on"
            write_policy(state)
            notify(f"🧪 Unit tests turned {'ON' if state['unit_tests'] else 'OFF'}. Applies from the next task. "
                   + ("Send /plan add test coverage for what's stable, if you want dedicated test tasks." if state["unit_tests"] else ""))
        elif cmd == "/plan":
            state["phase"] = "plan"
            state["feedback"] = rest or "Plan the next milestone(s) from SPEC.md."
            notify("📋 Re-planning with: " + state["feedback"])
        elif cmd == "/help":
            notify("/ask <question>: ask about the project, code, or libraries (reply to an answer to follow up; /new resets)\n"
                   "/status · /pause · /resume · /approve · /plan <request> · /tests on|off · /note <text>\n"
                   "Any other text: answers a blocked task (prefix 'T-007:' if several are blocked), "
                   "plan feedback while a plan awaits approval, otherwise a note in .work/inbox.md for all agents.")
        else:
            route_text(state, text, msg_id, reply_to)
    apply_qa_actions(state)
    save_state(state)


def answer_task(state, tid, text):
    tdir = WORK / "tasks" / tid
    tdir.mkdir(parents=True, exist_ok=True)
    with open(tdir / "answers.md", "a") as f:
        f.write(f"\n## {now():%Y-%m-%d %H:%M}\n{text}\n")
    q = tdir / "needs_human.md"
    if q.exists():
        q.rename(tdir / f"needs_human.answered-{now():%Y%m%d%H%M%S}.md")
    if any(t["id"] == tid and t["status"] == "!" for t in tasks()):
        set_status(tid, " ")
    notify(f"👍 Passed to {tid}; it's unblocked and will continue.")


def add_note(text):
    with open(WORK / "inbox.md", "a") as f:
        f.write(f"\n- ({now():%Y-%m-%d %H:%M}) {text}\n")


def plan_feedback(state, text):
    if state["phase"] != "await_approval":
        add_note(text)
        return
    state["feedback"] = (state.get("feedback", "") + "\n" + text).strip()
    state["phase"] = "plan"
    notify("📝 Revising the plan with your feedback.")


def route_text(state, text, msg_id=None, reply_to=None):
    """Explicit task answers go straight through; everything else is a conversation with the Q&A agent,
    which can turn it into a note / task answer / plan feedback via directives (see QA_SYSTEM)."""
    blocked = {t["id"] for t in tasks() if t["status"] == "!"}
    m = re.match(r"^(T-\d+)\s*[:\-]\s*(.+)$", text, re.S)
    if m and m[1] in blocked:
        return answer_task(state, m[1], m[2])
    tid = state.get("blocked_msgs", {}).get(str(reply_to)) if reply_to else None
    if tid and tid in blocked:
        return answer_task(state, tid, text)
    threading.Thread(target=qa_answer, args=(text, msg_id, qa_context(state)), daemon=True).start()


def qa_context(state):
    lines = [f"phase={state['phase']}", f"current_task={state.get('current')}"]
    if state["phase"] == "await_approval":
        lines.append("A plan (.work/backlog.md) is waiting for the owner's approval.")
    for t in tasks():
        q = WORK / "tasks" / t["id"] / "needs_human.md"
        if t["status"] == "!" and q.exists():
            lines.append(f"BLOCKED {t['id']} asks: {q.read_text()[:800]}")
    return "\n".join(lines)


# ---------------------------------------------------------------- Q&A (/ask)

QA_F = WORK / "qa.json"
QA_LOCK = threading.Lock()
QA_SYSTEM = """You answer the project owner's questions over Telegram. They are a software engineer building Voice Room
(see SPEC.md, CLAUDE.md) and want to understand the project, the code being written, and the underlying tech
(speaker embeddings, VAD, realtime voice APIs, libraries chosen in .work/decisions.md), sometimes to learn it properly.
Use the repo (.work/backlog.md, .work/STATUS.md, .work/tasks/*/summary.md, src/, git history files) and the web.
You are read-only: never modify files. Agents are building the project concurrently, so don't interfere.
Telegram shows plain text. No markdown tables or headings. Short paragraphs and simple '-' lists are fine.
Be concise by default (under ~250 words). Go deeper when asked to explain or teach. Link the official docs and sources you used.

Each message starts with a [harness] block describing the build's current state. You can't change files, but you can
ask the harness to act by putting a directive on its own line (one line each; it's stripped before the owner sees your reply):
@@NOTE: <instruction>         the owner wants to steer the build (priorities, preferences, scope). Saved to .work/inbox.md, read by every build agent.
@@ANSWER T-xxx: <answer>      the owner is answering a BLOCKED task's question. Restate the answer clearly and completely.
@@PLAN: <feedback>            the owner wants changes to a plan that's waiting for approval.
Only use a directive when the owner clearly means it. Questions and chat get a plain answer. If unsure, ask them.
Mention briefly in your reply what you passed on."""


def qa_load():
    try:
        return json.loads(QA_F.read_text())
    except (OSError, json.JSONDecodeError):
        return {"session": None, "msg_ids": []}


def qa_save(q):
    QA_F.write_text(json.dumps(q))


QA_ACTIONS = []  # filled by the Q&A thread, applied by the main loop (which owns the state)
DIRECTIVE_RE = re.compile(r"^\s*@@(NOTE|PLAN|ANSWER (T-\d+)):\s*(.+)$", re.M)


def apply_qa_actions(state):
    while QA_ACTIONS:
        kind, tid, text = QA_ACTIONS.pop(0)
        if kind == "NOTE":
            add_note(text)
        elif kind == "PLAN":
            plan_feedback(state, text)
        elif tid:
            answer_task(state, tid, text)


def qa_answer(question, msg_id, context=""):
    if QA_LOCK.locked():
        notify("⏳ Finishing the previous question first…", reply_to=msg_id)
    with QA_LOCK:
        tg("sendChatAction", chat_id=TG_CHAT, action="typing")
        q = qa_load()
        prompt = f"[harness]\n{context}\n[/harness]\n\n{question}" if context else question
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", CFG["models"].get("qa", "sonnet"),
               "--max-turns", "40", "--dangerously-skip-permissions", "--append-system-prompt", QA_SYSTEM,
               "--disallowedTools", ",".join(CFG["disallowed_tools"] + ["Write", "Edit", "NotebookEdit", "Bash"])]
        if q.get("session"):
            cmd += ["--resume", q["session"]]
        raw = ""
        try:
            raw = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=900).stdout
            res = json.loads(raw[raw.find("{"):]) if "{" in raw else {}
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            res = {"is_error": True, "result": f"failed: {e}"}
        text = str(res.get("result") or "").strip()
        if res.get("is_error") or not text:
            if LIMIT_RE.search(text + raw[-2000:]):
                text = "⏳ Usage limit reached. Ask again after it resets (the build waits too)."
            else:
                text = "⚠️ Couldn't answer: " + (text or "no output")[:500]
        if res.get("session_id"):
            q["session"] = res["session_id"]
        for m in DIRECTIVE_RE.finditer(text):
            QA_ACTIONS.append((m[1].split()[0], m[2], m[3].strip()))
            log(f"qa directive {m[1]}: {m[3][:120]}")
        text = DIRECTIVE_RE.sub("", text).strip() or "👍 Done."
        q["msg_ids"] = (q.get("msg_ids", []) + notify("💡 " + text, reply_to=msg_id))[-100:]
        qa_save(q)


def idle(state, seconds):
    end = time.time() + seconds
    while time.time() < end:
        handle(state, poll(state, timeout=int(min(30, max(1, end - time.time())))))


# ---------------------------------------------------------------- backlog

def tasks():
    if not BACKLOG.exists():
        return []
    lines = BACKLOG.read_text().splitlines()
    out = []
    for i, line in enumerate(lines):
        m = TASK_RE.match(line)
        if not m:
            continue
        body = [line]
        for nxt in lines[i + 1:]:
            if TASK_RE.match(nxt) or (nxt and not nxt[0].isspace()):
                break
            body.append(nxt)
        text = "\n".join(body).rstrip()
        dep = re.search(r"^\s*depends:\s*(.+)$", text, re.M)
        deps = re.findall(r"T-\d+", dep[1]) if dep else []
        out.append({"status": m[1], "id": m[2], "ms": m[3], "title": m[4].strip(), "body": text, "deps": deps})
    return out


def set_status(tid, ch):
    lines = BACKLOG.read_text().splitlines()
    for i, line in enumerate(lines):
        m = TASK_RE.match(line)
        if m and m[2] == tid:
            lines[i] = f"- [{ch}] {tid} | {m[3]} | {m[4]}"
    BACKLOG.write_text("\n".join(lines) + "\n")


def next_task(ts):
    done = {t["id"] for t in ts if t["status"] == "x"}
    for t in ts:  # resume interrupted work first
        if t["status"] == "~":
            return t
    for t in ts:
        if t["status"] == " " and all(d in done for d in t["deps"]):
            return t
    return None


def status_text(state):
    ts = tasks()
    done = sum(t["status"] == "x" for t in ts)
    lines = [f"Voice Room · {now():%Y-%m-%d %H:%M}",
             f"Phase: {state['phase']}{' (PAUSED)' if state.get('paused') else ''} · "
             f"unit tests {'on' if tests_on(state) else 'off'} · "
             f"{done}/{len(ts)} done · est. API-equivalent cost ${state.get('cost_usd', 0):.2f}"]
    if state.get("waiting_until"):
        lines.append(f"⏳ Usage limit, resuming ~{state['waiting_until']}")
    for t in ts:
        extra = ""
        q = WORK / "tasks" / t["id"] / "needs_human.md"
        if t["status"] == "!" and q.exists():
            extra = "\n     ❓ " + q.read_text().strip().splitlines()[0][:200]
        lines.append(f"{ICON[t['status']]} {t['id']} {t['title']}{extra}")
    return "\n".join(lines)


def write_status(state):
    recent = git("log", "--oneline", "-10")
    (WORK / "STATUS.md").write_text(
        "# Status\n\n```\n" + status_text(state) + "\n```\n\n## Recent commits\n\n```\n" + recent + "\n```\n")


# ---------------------------------------------------------------- claude

def parse_reset(text):
    """Best-effort: when does the usage limit reset? Returns datetime or None."""
    m = re.search(r"limit reached\|(\d{10})", text)
    if m:
        return datetime.datetime.fromtimestamp(int(m[1]))
    m = re.search(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.I)
    if m:
        h, mi, ap = int(m[1]), int(m[2] or 0), (m[3] or "").lower()
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h > 23:
            return None
        t = now().replace(hour=h, minute=mi, second=0, microsecond=0)
        return t if t > now() else t + datetime.timedelta(days=1)
    return None


def wait_for_limit(state, text):
    if re.search(r"overloaded|529", text, re.I):
        until = now() + datetime.timedelta(minutes=2)
    else:
        until = parse_reset(text) or now() + datetime.timedelta(minutes=CFG["limit_fallback_wait_min"])
        until = min(until, now() + datetime.timedelta(hours=8)) + datetime.timedelta(minutes=2)
        if not state.get("waiting_until"):
            notify(f"⏳ Usage limit hit. Sleeping until ~{until:%H:%M}, then continuing automatically.", progress=True)
    state["waiting_until"] = f"{until:%H:%M}"
    save_state(state)
    write_status(state)
    idle(state, max(30, (until - now()).total_seconds()))


def run_claude(state, key, prompt, model):
    """One headless Claude session per `key`, resumed across calls. Blocks through usage limits."""
    failures = 0
    while True:
        sid = state["sessions"].get(key)
        fresh = not sid
        if fresh:  # pick the id up front so a crash/restart mid-run can still resume this session
            sid = state["sessions"][key] = str(uuid.uuid4())
            save_state(state)
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model,
               "--max-turns", str(CFG["max_turns"]), "--dangerously-skip-permissions",
               "--disallowedTools", ",".join(CFG["disallowed_tools"])]
        cmd += ["--session-id", sid] if fresh else ["--resume", sid]
        LOGS.mkdir(parents=True, exist_ok=True)
        logf = LOGS / f"{key}-{now():%Y%m%d-%H%M%S}.json"
        log(f"claude[{key}] model={model} {'new session' if fresh else 'resume'} {sid[:8]}")
        with open(logf, "w") as out:
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, text=True)
            deadline = time.time() + CFG["task_timeout_min"] * 60
            while proc.poll() is None:
                handle(state, poll(state, timeout=20))
                if time.time() > deadline:
                    proc.kill()
                    log(f"claude[{key}] timed out")
        raw = logf.read_text()
        if not fresh and "No conversation found" in raw:  # session never got written; start it fresh
            log(f"claude[{key}] session {sid} not found, starting fresh")
            state["sessions"].pop(key, None)
            continue
        try:
            res = json.loads(raw[raw.find("{"):]) if "{" in raw else {}
        except json.JSONDecodeError:
            res = {}
        if res.get("session_id"):
            state["sessions"][key] = res["session_id"]
        state["cost_usd"] = state.get("cost_usd", 0) + (res.get("total_cost_usd") or 0)
        save_state(state)
        text = str(res.get("result") or "") + "\n" + raw[-3000:]
        if (res.get("is_error") or not res) and LIMIT_RE.search(text):
            wait_for_limit(state, text)
            prompt = "You were interrupted by a usage limit. Continue exactly where you left off."
            continue
        state.pop("waiting_until", None)
        if not res:
            failures += 1
            if failures >= 3:
                return {"is_error": True, "result": raw[-2000:]}
            log(f"claude[{key}] produced no result, retrying in 60s")
            idle(state, 60)
            continue
        return res


# ---------------------------------------------------------------- verification

def verify_cmd(state):
    return CFG["verify_cmd_with_tests"] if tests_on(state) else CFG["verify_cmd"]


def verify(state):
    r = subprocess.run(verify_cmd(state), shell=True, cwd=ROOT, capture_output=True, text=True, timeout=3600)
    return r.returncode == 0, (r.stdout + r.stderr)[-4000:]


def codex_review(state, tid, base, rnd):
    if not shutil.which("codex"):
        return None
    out = WORK / "tasks" / tid / f"review-codex-{rnd}.md"
    prompt = (OPS / "prompts" / "codex_review.md").read_text().format(
        task_id=tid, base=base, out=out.relative_to(ROOT), policy=policy(state))
    stage()
    try:
        r = subprocess.run(CFG["codex_cmd"] + [prompt], cwd=ROOT, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return None
    if not out.exists():
        if r.returncode != 0:
            log(f"codex review failed: {r.stderr[-500:]}")
            return None
        out.write_text(r.stdout[-6000:])
    stage()
    return out.read_text()


# ---------------------------------------------------------------- phases

def do_plan(state):
    fb = state.get("feedback") or ""
    if "plan" in state["sessions"] and fb:
        prompt = f"Human feedback on your plan. Revise .work/backlog.md accordingly:\n{fb}"
    else:
        prompt = (OPS / "prompts" / "plan.md").read_text().format(
            milestones=", ".join(CFG["milestones"]), policy=policy(state),
            feedback=f"Human request/feedback:\n{fb}" if fb else "")
    notify("📋 Planning" + (" (revising)" if fb else "") + "…", progress=True)
    res = run_claude(state, "plan", prompt, CFG["models"]["plan"])
    if not tasks():
        notify("⚠️ Planner produced no parsable backlog. Retrying in 10 min.\n" + str(res.get("result", ""))[:500], progress=True)
        state["sessions"].pop("plan", None)
        save_state(state)
        idle(state, 600)
        return
    commit("plan: update backlog")
    state["feedback"] = ""
    if CFG["require_plan_approval"]:
        state["phase"] = "await_approval"
        notify("📋 Plan ready (details: .work/backlog.md)\n\n" + str(res.get("result", ""))[:1200] + "\n\n"
               + "\n".join(f"⬜ {t['id']} [{t['ms']}] {t['title']}" for t in tasks() if t["status"] == " ")
               + "\n\nReply /approve, or send feedback to revise.")
    else:
        state["phase"] = "build"
    save_state(state)


def do_task(state, t):
    tid = t["id"]
    tdir = WORK / "tasks" / tid
    tdir.mkdir(parents=True, exist_ok=True)
    ensure_branch(t["ms"])
    resuming = tid in state["sessions"]  # only a saved session has the task context to "continue"
    set_status(tid, "~")
    state["current"] = tid
    base = state["bases"].setdefault(tid, git("rev-parse", "HEAD"))
    save_state(state)
    write_status(state)
    notify(f"{'🔁 Resuming' if resuming else '🔨 Starting'} {tid}: {t['title']}", progress=True)

    answers = (tdir / "answers.md").read_text() if (tdir / "answers.md").exists() else "(none)"
    if resuming:
        prompt = ("Continue task " + tid + ". Re-check .work/inbox.md, .work/POLICY.md and these human answers, then finish the task "
                  "following the original process:\n" + answers)
    else:
        inbox = (WORK / "inbox.md").read_text() if (WORK / "inbox.md").exists() else "(none)"
        prompt = (OPS / "prompts" / "task.md").read_text().format(
            id=tid, title=t["title"], ms=t["ms"], body=t["body"], inbox=inbox, answers=answers, policy=policy(state))

    feedback = ""
    for rnd in range(1, CFG["max_fix_rounds"] + 2):
        if feedback:
            prompt = (f"Fix round {rnd} for {tid}. Address the following, re-run `make check`, "
                      f"update .work/tasks/{tid}/summary.md, then stop.\n\n{feedback}")
        res = run_claude(state, tid, prompt, CFG["models"]["build"])

        if (tdir / "needs_human.md").exists():
            set_status(tid, "!")
            commit(f"wip({tid}): blocked, awaiting human")
            ids = notify(f"⛔ {tid} needs you:\n\n{(tdir / 'needs_human.md').read_text()[:3000]}\n\n"
                         "Reply to this message to answer. I'll continue with other tasks meanwhile.")
            state.setdefault("blocked_msgs", {}).update({str(i): tid for i in ids})
            save_state(state)
            return

        ok, out = verify(state)
        if not ok:
            feedback = f"`{verify_cmd(state)}` fails:\n```\n{out}\n```"
            log(f"{tid}: verify failed (round {rnd})")
            continue
        review = codex_review(state, tid, base, rnd)
        if review and re.search(r"VERDICT:\s*FAIL", review):
            feedback = "An independent reviewer (different model) found blocking issues:\n" + review[:6000]
            log(f"{tid}: codex review FAIL (round {rnd})")
            continue

        set_status(tid, "x")
        commit(f"{tid}: {t['title']}")
        state["current"] = None
        save_state(state)
        summary = str(res.get("result", "")).strip()[:1500]
        rv = "Codex review: PASS" if review else "Codex review: skipped (unavailable)"
        ms_tasks = [x for x in tasks() if x["ms"] == t["ms"]]
        if all(x["status"] == "x" for x in ms_tasks) and t["ms"] not in state.setdefault("milestones_done", []):
            state["milestones_done"].append(t["ms"])
            save_state(state)
            notify(f"🎉 {t['ms']} complete ({len(ms_tasks)} tasks, one commit each). Ready for your review; it gets pushed "
                   f"to GitHub within ~2 min:\n{review_url(t['ms'])}\n\nWork continues on the next milestone meanwhile.")
        notify(f"✅ {tid} done: {t['title']}\n\n{summary}\n\n{rv} · total est. ${state['cost_usd']:.2f}", progress=True)
        return

    set_status(tid, "!")
    (tdir / "needs_human.md").write_text(
        f"Couldn't converge after {CFG['max_fix_rounds']} fix rounds. How should I proceed? "
        f"(e.g. 'skip', a hint, or a scope change)\n\nLast problem:\n{feedback[:2500]}\n")
    commit(f"wip({tid}): blocked, did not converge")
    ids = notify(f"⛔ {tid} is stuck after {CFG['max_fix_rounds']} fix rounds.\n\n{feedback[:2500]}\n\n"
                 "Reply to this message with guidance.")
    state.setdefault("blocked_msgs", {}).update({str(i): tid for i in ids})
    save_state(state)


def main():
    WORK.mkdir(exist_ok=True)
    (WORK / "tasks").mkdir(exist_ok=True)
    if not git("rev-parse", "--verify", "-q", "HEAD"):
        commit("chore: initial scaffold")
    state = load_state()
    if TG_TOKEN and not state["telegram_offset"]:  # first start: ignore messages sent before the agent existed
        r = tg("getUpdates", offset=-1)
        if r and r.get("result"):
            state["telegram_offset"] = r["result"][-1]["update_id"] + 1
            save_state(state)
    write_policy(state)
    notify(f"🍳 Cook started (phase: {state['phase']}). Send /help for commands.", progress=True)
    announced_done = False
    code_mtime = os.path.getmtime(__file__)
    while True:
        if os.path.getmtime(__file__) != code_mtime and not QA_LOCK.locked():
            log("harness code changed, reloading")
            save_state(state)
            os.execv(sys.executable, [sys.executable, __file__])
        handle(state, poll(state))
        write_status(state)
        if state.get("paused"):
            idle(state, 60)
            continue
        if state["phase"] == "plan":
            do_plan(state)
            announced_done = False
            continue
        if state["phase"] == "await_approval":
            idle(state, 60)
            continue
        ts = tasks()
        t = next_task(ts)
        if t:
            do_task(state, t)
            announced_done = False
            continue
        if not announced_done:
            if ts and all(x["status"] == "x" for x in ts):
                notify(f"🎉 All {len(ts)} tasks done. Send /plan <what next> to continue.")
            else:
                notify("💤 Nothing runnable. Waiting on blocked tasks (see /status).")
            announced_done = True
        idle(state, 120)


if __name__ == "__main__":
    main()
