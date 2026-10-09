"""Local fake of the OpenAI Realtime WebSocket server (records client events, streams a tone).

Not a smoke script itself (no `smoke_` prefix). Run standalone: python fake_realtime_server.py
"""

import argparse
import asyncio
import base64
import json

import numpy as np
from websockets.asyncio.server import ServerConnection, serve

RATE = 24000
DELTAS = 6
DELTA_S = 0.1
DELTA_GAP_S = 0.05
TONE_SAMPLES = int(RATE * DELTA_S) * DELTAS


def _delta_b64() -> str:
    t = np.arange(int(RATE * DELTA_S)) / RATE
    pcm = (3000 * np.sin(2 * np.pi * 440 * t)).astype("<i2")
    return base64.b64encode(pcm.tobytes()).decode("ascii")


class FakeServer:
    def __init__(self) -> None:
        self.events: list[dict] = []  # audio payloads replaced by their length
        self.headers: list[dict] = []
        self.cancel_delay = 0.0  # simulate a slow cancel acknowledgement
        self._server = None
        self._n = 0
        self.url = ""

    async def _handler(self, ws: ServerConnection) -> None:
        self.headers.append({k.lower(): v for k, v in ws.request.headers.items()})
        await ws.send(json.dumps({"type": "session.created", "session": {}}))
        streams: dict[str, asyncio.Task] = {}
        active: set[str] = set()
        try:
            async for raw in ws:
                ev = json.loads(raw)
                rec = dict(ev)
                if "audio" in rec:
                    rec["audio"] = len(base64.b64decode(rec["audio"]))
                self.events.append(rec)
                t = ev["type"]
                if t == "session.update":
                    await ws.send(json.dumps({"type": "session.updated", "session": {}}))
                elif t == "response.create":
                    if any(r in active for r in streams):
                        await ws.send(json.dumps({"type": "error", "error": {
                            "type": "invalid_request_error",
                            "code": "conversation_already_has_active_response",
                            "message": "active response",
                            "event_id": ev.get("event_id")}}))  # fmt: skip
                        continue
                    self._n += 1
                    rid = f"resp_{self._n}"
                    active.add(rid)
                    await ws.send(json.dumps({"type": "response.created", "response": {
                        "id": rid, "status": "in_progress"}}))  # fmt: skip
                    streams[rid] = asyncio.create_task(self._stream(ws, rid, active))
                elif t == "response.cancel":
                    await asyncio.sleep(self.cancel_delay)
                    live = [r for r in streams if r in active]
                    if not live:
                        await ws.send(json.dumps({"type": "error", "error": {
                            "type": "invalid_request_error", "code": "response_cancel_not_active",
                            "message": "no active response"}}))  # fmt: skip
                    for rid in live:
                        active.discard(rid)
                        streams[rid].cancel()
                        await ws.send(json.dumps({"type": "response.done", "response": {
                            "id": rid, "status": "cancelled"}}))  # fmt: skip
        finally:
            for task in streams.values():
                task.cancel()

    async def _stream(self, ws: ServerConnection, rid: str, active: set[str]) -> None:
        item = f"item_{rid}"
        send = lambda d: ws.send(json.dumps(d))  # noqa: E731
        await send({"type": "response.output_item.added", "response_id": rid, "item": {"id": item}})
        for _ in range(DELTAS):
            await asyncio.sleep(DELTA_GAP_S)
            await send({
                "type": "response.output_audio.delta", "response_id": rid, "item_id": item,
                "output_index": 0, "content_index": 0, "delta": _delta_b64(),
            })  # fmt: skip
        await send({"type": "response.output_audio.done", "response_id": rid, "item_id": item})
        active.discard(rid)
        await send({"type": "response.done", "response": {"id": rid, "status": "completed"}})

    async def close(self) -> None:
        self._server.close()
        await self._server.wait_closed()


async def serve_fake(host: str = "127.0.0.1", port: int = 0) -> FakeServer:
    fake = FakeServer()
    fake._server = await serve(fake._handler, host, port)
    fake.url = f"ws://{host}:{fake._server.sockets[0].getsockname()[1]}"
    return fake


async def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    fake = await serve_fake(port=ap.parse_args().port)
    print("listening on", fake.url)
    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(_main())
