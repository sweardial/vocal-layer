"""MANUAL live check (real API, costs money; not part of `make check`).

Sends one wav as a turn to OpenAI Realtime and saves the reply to data/out/openai_reply.wav.
Needs OPENAI_API_KEY in the environment.  Usage:
    uv run python scripts/live/openai_realtime_check.py --wav data/some.wav [--label alice]
"""

import argparse
import asyncio
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from voiceroom.adapters.openai_realtime import DEFAULT_MODEL, OpenAIRealtimeProvider
from voiceroom.audio import load_audio

OUT = Path("data/out/openai_reply.wav")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--label", default=None)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    p = OpenAIRealtimeProvider(model=args.model)
    await p.connect()
    out = p.audio_out()
    t0 = time.monotonic()
    await p.send_turn(load_audio(args.wav), args.label)
    parts: list[np.ndarray] = []
    rate = 24000
    async with asyncio.timeout(60):
        async for c in out:
            if not parts:
                print(f"first audio after {time.monotonic() - t0:.2f}s")
            parts.append(c.samples)
            rate = c.sample_rate
            if c.final:
                break
    await p.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    audio = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    sf.write(str(OUT), audio, rate)
    print(f"saved {len(audio) / rate:.2f}s of reply to {OUT}")


asyncio.run(main())
