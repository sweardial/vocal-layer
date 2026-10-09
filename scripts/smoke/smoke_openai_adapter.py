"""Smoke: OpenAIRealtimeProvider against the local fake Realtime server (no network)."""

import asyncio
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from fake_realtime_server import TONE_SAMPLES, serve_fake  # noqa: E402

from voiceroom.adapters import OpenAIRealtimeProvider  # noqa: E402

SECRET = "sk-test-SECRET"


def types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def collapse(ts: list[str]) -> list[str]:
    return [
        t for i, t in enumerate(ts) if i == 0 or t != ts[i - 1] or t != "input_audio_buffer.append"
    ]


async def collect(out, until_final: bool = True):
    chunks = []
    async with asyncio.timeout(5):
        async for c in out:
            chunks.append(c)
            if c.final:
                break
    return chunks


async def main() -> None:
    import os

    os.environ["OPENAI_API_KEY"] = SECRET
    logs: list[str] = []
    h = logging.Handler()
    h.emit = lambda r: logs.append(r.name + ": " + r.getMessage())
    logging.getLogger().addHandler(h)
    logging.getLogger().setLevel(logging.DEBUG)

    fake = await serve_fake()
    p = OpenAIRealtimeProvider(url=fake.url)
    await p.connect()
    assert SECRET not in repr(p)
    assert fake.headers[0]["authorization"] == f"Bearer {SECRET}"
    assert "openai-beta" not in fake.headers[0]

    su = fake.events[0]
    assert su["type"] == "session.update" and su["session"]["type"] == "realtime", su
    inp = su["session"]["audio"]["input"]
    assert inp["turn_detection"] is None and inp["format"] == {"type": "audio/pcm", "rate": 24000}

    out = p.audio_out()
    rng = np.random.default_rng(0)
    turns = [("owner", 1.0, True), ("guest", 1.0, False), (None, 0.5, True)]
    for label, secs, accepted in turns:  # a dropped turn never reaches the provider
        audio = (0.1 * rng.standard_normal(int(16000 * secs))).astype(np.float32)
        if not accepted:
            continue
        start = len(fake.events)
        await p.send_turn(audio, label)
        chunks = await collect(out)
        evs = fake.events[start:]
        ts = collapse(types(evs))
        want = ["input_audio_buffer.append", "input_audio_buffer.commit", "response.create"]
        if label:
            want = ["conversation.item.create"] + want
            assert evs[0]["item"]["content"] == [
                {"type": "input_text", "text": f"[Speaker: {label}]"}
            ]
            assert evs[0]["item"]["role"] == "user"
        assert ts == want, ts
        nbytes = sum(e["audio"] for e in evs if e["type"] == "input_audio_buffer.append")
        assert abs(nbytes - secs * 24000 * 2) <= 4, nbytes
        assert all(c.sample_rate == 24000 for c in chunks) and chunks[-1].final
        assert sum(len(c.samples) for c in chunks) == TONE_SAMPLES
        assert max(np.abs(c.samples).max() for c in chunks if len(c.samples)) > 0.05
    assert types(fake.events).count("input_audio_buffer.commit") == 2  # guest dropped
    assert types(fake.events).count("conversation.item.create") == 1

    n = len(fake.events)
    await p.interrupt(50)  # reply finished but still "playing": truncate only, no cancel
    await asyncio.sleep(0.1)
    assert types(fake.events[n:]) == ["conversation.item.truncate"], types(fake.events[n:])
    await p.interrupt(50)  # fully idle: nothing to send
    await asyncio.sleep(0.1)
    assert len(fake.events) == n + 1
    n += 1

    try:
        await p.send_turn(np.zeros(0, np.float32), "x")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert len(fake.events) == n

    # barge-in mid-reply
    await p.send_turn(np.full(8000, 0.1, np.float32), "owner")
    first = await asyncio.wait_for(anext(out), 5)
    second = await asyncio.wait_for(anext(out), 5)
    await p.interrupt(50)
    await asyncio.sleep(0.4)  # let the fake server's remaining deltas (if any) arrive
    tail = fake.events[-2:]
    assert types(tail) == ["response.cancel", "conversation.item.truncate"], types(tail)
    assert tail[1]["content_index"] == 0 and tail[1]["audio_end_ms"] == 50, tail[1]
    assert tail[1]["item_id"].startswith("item_resp_")
    assert first.reply_id == second.reply_id
    # a new turn while a reply is running cancels it first (API allows one response at a time)
    await p.send_turn(np.full(8000, 0.1, np.float32), "bob")
    await asyncio.sleep(0.1)
    m = len(fake.events)
    await p.send_turn(np.full(8000, 0.1, np.float32), "alice")
    await asyncio.sleep(0.1)
    sup = collapse(types(fake.events[m:]))
    assert sup[0] == "response.cancel" and sup[-1] == "response.create", sup
    mid = await collect(out)
    assert mid[-1].final and sum(len(c.samples) for c in mid) == TONE_SAMPLES, len(mid)
    assert len({c.reply_id for c in mid}) == 1
    assert not any(e.get("code") for e in fake.events if e["type"] == "error")
    # next reply gets a fresh id and nothing from the cancelled one leaks through
    await p.send_turn(np.full(8000, 0.1, np.float32), None)
    nxt = await collect(out)
    assert all(c.reply_id > first.reply_id for c in nxt), [c.reply_id for c in nxt]
    assert sum(len(c.samples) for c in nxt) == TONE_SAMPLES

    # back-to-back turns with no waits: only the last reply survives, then the next one works
    for label in ("a", "b", "c"):
        await p.send_turn(np.full(8000, 0.1, np.float32), label)
    last = await collect(out)
    assert last[-1].final and sum(len(c.samples) for c in last) == TONE_SAMPLES, len(last)
    assert len({c.reply_id for c in last}) == 1
    await p.send_turn(np.full(8000, 0.1, np.float32), "d")
    fourth = await collect(out)
    assert sum(len(c.samples) for c in fourth) == TONE_SAMPLES
    assert fourth[0].reply_id > last[0].reply_id

    await p.close()
    assert [c async for c in out] == []
    await fake.close()
    bad = [m[:80] for m in logs if SECRET in m and not m.startswith("websockets.server")]
    assert not bad, bad

    # reconnect right after a send (response.created not yet seen): no stale state
    fake = await serve_fake()
    p = OpenAIRealtimeProvider(url=fake.url)
    await p.connect()
    await p.send_turn(np.full(8000, 0.1, np.float32), "x")
    await p.close()
    await p.connect()
    n = len(fake.events)
    out = p.audio_out()
    await p.send_turn(np.full(8000, 0.1, np.float32), "y")
    got = await collect(out)
    assert "response.cancel" not in types(fake.events[n:])
    assert sum(len(c.samples) for c in got) == TONE_SAMPLES

    # slow cancel acknowledgement: fail and drop the connection, never yield late cancelled audio
    m_ = __import__("voiceroom.adapters.openai_realtime", fromlist=["x"])
    m_.CANCEL_WAIT_S = 0.3
    fake.cancel_delay = 0.8
    await p.send_turn(np.full(8000, 0.1, np.float32), "slow")
    try:
        await p.send_turn(np.full(8000, 0.1, np.float32), "slower")
        raise AssertionError("expected ConnectionError")
    except ConnectionError:
        pass
    assert [c async for c in out] == []  # stream ended, nothing from the cancelled reply
    m_.CANCEL_WAIT_S = 5.0
    fake.cancel_delay = 0.0
    await p.connect()
    out = p.audio_out()

    # interrupted send (cancelled around response.create): connection is torn down, reconnect works
    real_send = p._send

    async def hang_on_create(ev):
        if ev["type"] == "response.create":
            await asyncio.sleep(10)
        await real_send(ev)

    p._send = hang_on_create
    try:
        await asyncio.wait_for(p.send_turn(np.full(8000, 0.1, np.float32), "z"), 0.3)
        raise AssertionError("expected timeout")
    except TimeoutError:
        pass
    assert not p._pending and p._ws is None
    del p._send
    await p.connect()
    out = p.audio_out()
    n = len(fake.events)
    await p.send_turn(np.full(8000, 0.1, np.float32), "ok")
    got = await collect(out)
    assert sum(len(c.samples) for c in got) == TONE_SAMPLES, (
        [(len(c.samples), c.final) for c in got],
        types(fake.events[n:]),
    )
    assert "response.cancel" not in types(fake.events[n:])

    # stalled consumer: buffered audio is bounded, finals are kept
    from voiceroom.adapters import openai_realtime as m

    m.MAX_BUFFER_S = 1.0
    m.MAX_BUFFER_ENTRIES = 5
    for _ in range(4):
        await p.send_turn(np.full(8000, 0.1, np.float32), None)
        await asyncio.sleep(1.0)  # each reply completes unconsumed
    held = [c for c in p._buf if c is not None]
    assert sum(len(c.samples) for c in held) <= 1.0 * 24000, len(held)
    assert held[-1].final and len(p._buf) <= 5, len(p._buf)
    await p.close()
    await fake.close()

    try:
        os.environ.pop("OPENAI_API_KEY")
        await OpenAIRealtimeProvider().connect()
        raise AssertionError("expected RuntimeError without key")
    except RuntimeError:
        pass


asyncio.run(main())
print("ok")
