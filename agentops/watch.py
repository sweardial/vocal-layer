#!/usr/bin/env python3
"""Live view of what the agents are doing: follows Claude Code session transcripts (main + subagents)."""
import datetime
import glob
import json
import os
import sys
import time

PROJ = os.path.expanduser("~/.claude/projects/-workspace")
DIM, BOLD, CYAN, YEL, GRN, RST = "\033[2m", "\033[1m", "\033[36m", "\033[33m", "\033[32m", "\033[0m"


def short(inp):
    for k in ("command", "file_path", "pattern", "url", "query", "description", "prompt"):
        if k in inp:
            return str(inp[k]).replace("\n", " ")[:110]
    return json.dumps(inp)[:110]


def label(path):
    if "/subagents/" in path:
        return f"{YEL}  ↳ sub-{os.path.basename(path)[6:12]}{RST}"
    return f"{CYAN}main{RST}"


def render(path, line):
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return
    m = d.get("message")
    if not isinstance(m, dict) or not isinstance(m.get("content"), list):
        return
    ts = d.get("timestamp", "")
    try:
        t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except ValueError:
        t = "--:--:--"
    for c in m["content"]:
        if d.get("type") == "assistant" and c.get("type") == "tool_use":
            print(f"{DIM}{t}{RST} {label(path)} {BOLD}{c['name']}{RST} {short(c.get('input', {}))}")
        elif d.get("type") == "assistant" and c.get("type") == "text" and c["text"].strip():
            txt = c["text"].strip().replace("\n", " ")
            print(f"{DIM}{t}{RST} {label(path)} {GRN}💬 {txt[:220]}{'…' if len(txt) > 220 else ''}{RST}")


def main():
    history = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    offsets = {}
    files = sorted(glob.glob(f"{PROJ}/**/*.jsonl", recursive=True), key=os.path.getmtime)
    for f in files:  # start at the end of everything except the newest session's tail
        offsets[f] = os.path.getsize(f)
    if files:
        newest = [f for f in files if "/subagents/" not in f][-1:] or files[-1:]
        with open(newest[0]) as fh:
            lines = fh.readlines()
        print(f"{DIM}— last activity —{RST}")
        for line in lines[-history * 3:]:
            render(newest[0], line)
    print(f"{DIM}— live (Ctrl+C to exit) —{RST}")
    while True:
        for f in glob.glob(f"{PROJ}/**/*.jsonl", recursive=True):
            size = os.path.getsize(f)
            pos = offsets.get(f, 0)
            if size > pos:
                with open(f) as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                complete, _, _rest = chunk.rpartition("\n")
                for line in complete.splitlines():
                    render(f, line)
                offsets[f] = pos + len(complete.encode()) + (1 if complete else 0)
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
