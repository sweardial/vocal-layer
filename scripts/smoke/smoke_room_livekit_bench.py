"""Smoke: the LiveKit latency bench on 1 tiny synthetic conversation: FakeRoom and the local
livekit-server both commit turns, the report section renders and splices. SKIPs without the
binary or the `livekit` extra."""

import asyncio
import sys

SKIP = 77
try:
    import livekit.rtc  # noqa: F401
except ImportError:
    print("SKIP: `livekit` extra not installed")
    sys.exit(SKIP)
from voiceroom.adapters.livekit_server import find_binary, run_dev_server  # noqa: E402

if find_binary() is None:
    print("SKIP: livekit-server not found (scripts/fetch_livekit.py)")
    sys.exit(SKIP)

from voiceroom.bench import room as R  # noqa: E402
from voiceroom.bench import room_livekit as RL  # noqa: E402
from voiceroom.embedders import get_embedder  # noqa: E402
from voiceroom.eval.corpus import SynthCorpus  # noqa: E402
from voiceroom.vad import EnergyVAD  # noqa: E402

R.LENGTHS = (1.3, 1.8)  # short turns: the smoke streams in realtime
RL.VAD_FACTORY = EnergyVAD
RL.SETTLE_S = 1.0
emb = get_embedder("toy")
work = R.make_work(SynthCorpus(8, seed=1, utts_per_speaker=14), emb, 1, 4, 0, True, n_enroll=6)
conv, sigs, claims, members = work[0]
assert conv.segs
with run_dev_server() as server:

    async def go():
        f, k = await asyncio.gather(  # concurrent: the smoke has no baseline to protect
            RL.run_fake(conv, sigs, claims, members, emb, 0.0),
            RL.run_livekit(server, 0, conv, sigs, claims, members, emb, 0.0),
        )
        await RL.settle()
        return f, k

    fake, lk = asyncio.run(go())
fm, lm = RL.Metrics(), RL.Metrics()
RL.measure(conv, claims, fake, fm)
RL.measure(conv, claims, lk, lm)
assert fm.commits and lm.commits, (fm.commits, lm.commits)
assert lm.to_heard, "listener never heard a reply"
text = RL.render(fm, lm, "smoke", 1.0)
assert RL.SECTION in text and "Reply round trip" in text
base = "# Room benchmark: x\n\n## a\n\nold\n\n" + RL.SECTION + "\n\nstale\n\n## Verify\n\nkeep\n"
out = RL.splice(base, text)
assert "stale" not in out and "keep" in out and out.count(RL.SECTION) == 1
assert fm.e2e and lm.e2e, "no commit matched a segment"
assert all(-0.2 < x < 2.0 for x in fm.e2e + lm.e2e), (fm.e2e, lm.e2e)
assert max(lm.wait) < 0.1, lm.wait  # no artificial catch-up wait from track clock skew
print(f"commits fake={fm.commits} lk={lm.commits} matched={len(lm.e2e)} heard={len(lm.to_heard)}")
