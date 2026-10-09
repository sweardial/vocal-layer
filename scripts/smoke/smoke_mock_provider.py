"""Smoke: MockProvider records 3 turns in order, replies with audio, honors interrupt; no
provider-specific imports outside adapters/."""

import ast
import asyncio
import sys
from pathlib import Path

import numpy as np

from voiceroom.adapters import MockProvider, Provider

SRC = Path(__file__).resolve().parents[2] / "src" / "voiceroom"
BANNED = {"openai", "websockets", "google", "livekit", "elevenlabs"}


async def main() -> None:
    p: Provider = MockProvider()
    await p.connect()
    labels = ["alice", None, "bob"]
    turns = [np.full(16000 * (i + 1), 0.1 * (i + 1), np.float32) for i in range(3)]
    out = p.audio_out()
    for audio, label in zip(turns, labels, strict=True):
        await p.send_turn(audio, label)
        got = [c async for c in _take(out, 3)]
        assert got[-1].final and not any(c.final for c in got[:-1])
        assert sum(len(c.samples) for c in got) > 0 and all(
            np.abs(c.samples).max() > 0 for c in got
        )
    rec = p.turns
    assert [r.speaker_label for r in rec] == labels, [r.speaker_label for r in rec]
    assert [len(r.audio) for r in rec] == [len(t) for t in turns]

    await p.send_turn(turns[0], "alice")
    first = await anext(out)
    await p.interrupt(120)
    assert p.interrupts == [120]
    await p.send_turn(turns[1], "bob")
    nxt = await anext(out)  # only the new reply remains
    assert nxt.reply_id > first.reply_id and not nxt.final and len(p.turns) == 5
    await p.close()
    await p.interrupt()  # no-op after close; must not swallow end-of-stream
    assert len([c async for c in out]) == 2  # iterator ends after close


async def _take(it, n):
    for _ in range(n):
        yield await anext(it)


def check_imports() -> None:
    for f in SRC.rglob("*.py"):
        if "adapters" in f.relative_to(SRC).parts:
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            bad = [n for n in names if n.split(".")[0] in BANNED]
            assert not bad, f"provider-specific import in {f}: {bad}"


asyncio.run(main())
check_imports()
print("ok")
sys.exit(0)
