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
import time
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


def commit(message):
    git("add", "-A")
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


def notify(text):
    log("NOTIFY " + text.replace("\n", " | ")[:300])
    if TG_CHAT:
        for i in range(0, len(text), 3900):
            tg("sendMessage", chat_id=TG_CHAT, text=text[i:i + 3900])


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
            msgs.append(m["text"].strip())
    save_state(state)
    return msgs


def handle(state, msgs):
    for text in msgs:
        cmd, _, rest = text.partition(" ")
        cmd = cmd.lower().split("@")[0]
        if cmd in ("/status", "/start"):
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
            notify("/status · /pause · /resume · /approve · /plan <request> · /tests on|off\n"
                   "Any other text: answers a blocked task (prefix 'T-007:' if several are blocked), "
                   "plan feedback while a plan awaits approval, otherwise a note in .work/inbox.md for all agents.")
        else:
            route_text(state, text)
    save_state(state)


def route_text(state, text):
    if state["phase"] == "await_approval":
        state["feedback"] = (state.get("feedback", "") + "\n" + text).strip()
        state["phase"] = "plan"
        notify("📝 Got it, revising the plan.")
        return
    blocked = [t for t in tasks() if t["status"] == "!"]
    m = re.match(r"^(T-\d+)\s*[:\-]\s*(.+)$", text, re.S)
    target = None
    if m and any(t["id"] == m[1] for t in blocked):
        target, text = m[1], m[2]
    elif len(blocked) == 1:
        target = blocked[0]["id"]
    if target:
        tdir = WORK / "tasks" / target
        with open(tdir / "answers.md", "a") as f:
            f.write(f"\n## {now():%Y-%m-%d %H:%M}\n{text}\n")
        q = tdir / "needs_human.md"
        if q.exists():
            q.rename(tdir / f"needs_human.answered-{now():%Y%m%d%H%M%S}.md")
        set_status(target, " ")
        notify(f"👍 Answer saved, {target} unblocked.")
    else:
        with open(WORK / "inbox.md", "a") as f:
            f.write(f"\n- ({now():%Y-%m-%d %H:%M}) {text}\n")
        hint = f" Several tasks are blocked ({', '.join(t['id'] for t in blocked)}); prefix with 'T-xxx:' to answer one." if blocked else ""
        notify("🗒 Added to inbox.md; agents will read it." + hint)


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
            notify(f"⏳ Usage limit hit. Sleeping until ~{until:%H:%M}, then continuing automatically.")
    state["waiting_until"] = f"{until:%H:%M}"
    save_state(state)
    write_status(state)
    idle(state, max(30, (until - now()).total_seconds()))


def run_claude(state, key, prompt, model):
    """One headless Claude session per `key`, resumed across calls. Blocks through usage limits."""
    failures = 0
    while True:
        sid = state["sessions"].get(key)
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model,
               "--max-turns", str(CFG["max_turns"]), "--dangerously-skip-permissions",
               "--disallowedTools", ",".join(CFG["disallowed_tools"])]
        if sid:
            cmd += ["--resume", sid]
        LOGS.mkdir(parents=True, exist_ok=True)
        logf = LOGS / f"{key}-{now():%Y%m%d-%H%M%S}.json"
        log(f"claude[{key}] model={model} resume={bool(sid)}")
        with open(logf, "w") as out:
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, text=True)
            deadline = time.time() + CFG["task_timeout_min"] * 60
            while proc.poll() is None:
                handle(state, poll(state, timeout=20))
                if time.time() > deadline:
                    proc.kill()
                    log(f"claude[{key}] timed out")
        raw = logf.read_text()
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
    git("add", "-A")
    try:
        r = subprocess.run(CFG["codex_cmd"] + [prompt], cwd=ROOT, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return None
    if not out.exists():
        if r.returncode != 0:
            log(f"codex review failed: {r.stderr[-500:]}")
            return None
        out.write_text(r.stdout[-6000:])
    git("add", "-A")
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
    notify("📋 Planning" + (" (revising)" if fb else "") + "…")
    res = run_claude(state, "plan", prompt, CFG["models"]["plan"])
    if not tasks():
        notify("⚠️ Planner produced no parsable backlog. Retrying in 10 min.\n" + str(res.get("result", ""))[:500])
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
    resuming = t["status"] == "~" or tid in state["sessions"]
    set_status(tid, "~")
    state["current"] = tid
    base = state["bases"].setdefault(tid, git("rev-parse", "HEAD"))
    save_state(state)
    write_status(state)
    notify(f"{'🔁 Resuming' if resuming else '🔨 Starting'} {tid}: {t['title']}")

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
            notify(f"⛔ {tid} needs you:\n\n{(tdir / 'needs_human.md').read_text()[:3000]}\n\nReply here to answer. I'll continue with other tasks meanwhile.")
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
        notify(f"✅ {tid} done: {t['title']}\n\n{summary}\n\n{rv} · total est. ${state['cost_usd']:.2f}")
        return

    set_status(tid, "!")
    (tdir / "needs_human.md").write_text(
        f"Couldn't converge after {CFG['max_fix_rounds']} fix rounds. How should I proceed? "
        f"(e.g. 'skip', a hint, or a scope change)\n\nLast problem:\n{feedback[:2500]}\n")
    commit(f"wip({tid}): blocked, did not converge")
    notify(f"⛔ {tid} is stuck after {CFG['max_fix_rounds']} fix rounds.\n\n{feedback[:2500]}\n\nReply with guidance.")


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
    notify(f"🍳 Cook started (phase: {state['phase']}). Send /help for commands.")
    announced_done = False
    while True:
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
