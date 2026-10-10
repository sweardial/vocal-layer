"""Smoke: the room demo script parses; the voiceroom commands and flags it and the doc use exist."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sh = ROOT / "scripts/live/run_room_demo.sh"
doc = (ROOT / "docs/demo-room.md").read_text()
assert subprocess.run(["bash", "-n", str(sh)]).returncode == 0, "run_room_demo.sh syntax"

help_ = {
    k: subprocess.run(
        ["uv", "run", "--no-sync", "voiceroom", *k.split(), "--help"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout
    for k in (
        "enroll",
        "session create",
        "session invite",
        "session accept",
        "session revoke",
        "token",
        "room",
    )
}
text = sh.read_text() + doc
for m in set(re.findall(r"voiceroom (enroll|session \w+|token|room)\b[^\n|]*", text)):
    assert help_.get(m), f"unknown subcommand {m}"
for flag in (
    "--transport",
    "--provider",
    "--debug",
    "--mode",
    "--owner",
    "--code",
    "--session",
    "--user",
):
    assert any(flag in h for h in help_.values()), flag
for must in (
    "uv sync --extra livekit",
    "fetch_livekit.py",
    "voiceroom token",
    "meet.livekit.io",
    "--debug",
):
    assert must in doc, must
print("smoke_demo_room OK")
