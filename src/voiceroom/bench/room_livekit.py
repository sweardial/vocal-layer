"""Room latency through the real LiveKit transport (`bench.room --transport livekit`).

The same multi-track conversations run twice through RoomAgent, in realtime and concurrently:
once over FakeRoom (in memory) and once over a local `livekit-server --dev` (WebRTC, jitter
buffer, Opus, SFU). Times are wall clock against the publisher's own clock, so the transport's
delay is inside the number: end of speech at the publisher -> `send_turn` (commit), and commit ->
first reply audio handed to the transport / heard by a listener participant. Writes the
"LiveKit transport" section of reports/room-<embedder>.md.
"""

import asyncio
import gc
import sys
import time
from dataclasses import dataclass, field, replace

import numpy as np

from voiceroom.adapters import MockProvider
from voiceroom.bench.pipeline import TARGET_S, _covered, ms
from voiceroom.bench.room import make_work
from voiceroom.config import threshold_for
from voiceroom.matcher import Matcher
from voiceroom.policy import TrackPolicy, Verdict
from voiceroom.room import RoomPipeline
from voiceroom.rooms import FakeRoom, RoomAgent
from voiceroom.rooms.fake import FakeParticipant

SECTION = "## LiveKit transport"
BOT = "voiceroom-bot"
END_TOL_S = 0.5  # a commit counts as a segment's if it ends this close to the segment's end
VAD_FACTORY = None  # smoke scripts swap in a cheap VAD; default SileroVAD
MIN_CONVERSATIONS = 5
SETTLE_S = 2.5  # after the last audio: the last commit and its reply


@dataclass
class ConvResult:
    recs: list
    provider: MockProvider
    t0: float  # wall time (monotonic) at which every track started streaming
    srcmap: dict[str, list]  # source id -> [claimed identity, wall time of first frame, offset]
    pub_times: dict[int, float]  # reply id -> first publish_audio call
    onsets: list[float] = field(default_factory=list)  # listener: reply onsets (LiveKit only)


@dataclass
class Metrics:
    e2e: list[float] = field(default_factory=list)  # true end of speech -> commit
    pipeline: list[float] = field(default_factory=list)  # RoomTurnRecord.latency_s
    endpoint: list[float] = field(default_factory=list)
    compute: list[float] = field(default_factory=list)  # incl. queue wait
    wait: list[float] = field(default_factory=list)
    transport: list[float] = field(default_factory=list)  # e2e - pipeline, per turn
    to_publish: list[float] = field(default_factory=list)  # commit -> first reply audio published
    to_heard: list[float] = field(default_factory=list)  # commit -> reply onset at the listener
    commits: int = 0
    unmatched: int = 0  # committed turns with no own/shared segment behind them
    replies: int = 0
    unheard: int = 0


def _timed(cls):
    """`cls` that remembers when each reply's first audio was handed to the transport."""

    class Timed(cls):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.pub_times: dict[int, float] = {}

        async def publish_audio(self, audio):
            self.pub_times.setdefault(audio.reply_id, time.monotonic())
            await super().publish_audio(audio)

    Timed.__name__ = f"Timed{cls.__name__}"
    return Timed


def _identity(claims: dict, tid: str) -> str:
    return claims[tid] or tid  # the shared mic is not a member: no claim


def _agent(transport, sigs, members, emb, verify):
    provider = MockProvider()

    def policy(sid: str, claimed: str | None):
        if claimed in members:
            return TrackPolicy(
                "verified", members, claimed, "drop", embedder=emb.name, verify_threshold=verify
            )
        return TrackPolicy("shared", members, None, "label", embedder=emb.name)

    pipeline = RoomPipeline(
        provider,
        policy,
        Matcher(sigs, threshold=threshold_for(emb.name), embedder=emb),
        max_pending=1000,
        isolation="thread",
        **({"vad_factory": VAD_FACTORY} if VAD_FACTORY else {}),
    )
    srcmap: dict[str, list] = {}
    add = pipeline.add_source

    def add_source(source_id, frames, claimed=None, *a, **k):
        entry = srcmap[source_id] = [claimed, None, 0.0]

        async def tap():  # note when the track's first frame arrives (wall)
            async for f in frames:
                if entry[1] is None:
                    entry[1] = time.monotonic()
                yield f

        out = add(source_id, tap(), claimed, *a, **k)  # production offsets (runner.py)
        entry[2] = pipeline.sources[source_id].offset
        return out

    pipeline.add_source = add_source  # type: ignore[method-assign]
    return RoomAgent(transport, pipeline, provider), pipeline, provider, srcmap


async def run_fake(conv, sigs, claims, members, emb, verify) -> ConvResult:
    box: dict[str, float] = {}

    class Room(_timed(FakeRoom)):
        async def connect(self):
            await super().connect()
            box["t0"] = time.monotonic()

    parts = [
        FakeParticipant(tid, audio, identity=_identity(claims, tid))
        for tid, audio in conv.tracks.items()
    ]
    room = Room(parts, realtime=True, self_identity=BOT)
    agent, pipeline, provider, srcmap = _agent(room, sigs, members, emb, verify)
    await agent.run()
    return ConvResult(list(pipeline.records), provider, box["t0"], srcmap, room.pub_times)


async def run_livekit(server, idx, conv, sigs, claims, members, emb, verify) -> ConvResult:
    from voiceroom.adapters.livekit_harness import stream_room
    from voiceroom.adapters.livekit_room import LiveKitRoom, make_token

    bot = _timed(LiveKitRoom)(
        server.url,
        make_token(
            server.url, f"bench{idx}", BOT, api_key=server.api_key, api_secret=server.api_secret
        ),
    )
    agent, pipeline, provider, srcmap = _agent(bot, sigs, members, emb, verify)
    agent_task = asyncio.create_task(agent.run())

    async def finish() -> None:
        await bot.close()  # ends the event stream; the agent drains and stops
        await asyncio.wait_for(agent_task, 15)

    t0, onsets = await stream_room(
        server,
        f"bench{idx}",
        {t: (_identity(claims, t), a) for t, a in conv.tracks.items()},
        BOT,
        lambda: len(pipeline.sources) >= len(conv.tracks),
        agent_task.done,
        finish,
        SETTLE_S,
    )
    return ConvResult(list(pipeline.records), provider, t0, srcmap, bot.pub_times, onsets)


def measure(conv, claims, res: ConvResult, m: Metrics) -> None:
    tid_of = {_identity(claims, t): t for t in conv.tracks}
    committed = [r for r in res.recs if r.verdict is Verdict.ACCEPT]
    turns = res.provider.turns
    assert len(committed) == len(turns)
    for k, (r, turn) in enumerate(zip(committed, turns, strict=True)):
        m.commits += 1
        claimed, first, offset = res.srcmap.get(r.source_id, (None, None, 0.0))
        tid = tid_of.get(claimed or "")
        # room time -> stream time -> publisher time (the first frame arrived at `first`)
        shift = (first - res.t0 - offset) if first is not None else -offset
        rt = replace(r, t_start=r.t_start + shift, t_end=r.t_end + shift)
        seg = next(
            (
                s
                for s in conv.segs
                if s.track == tid
                and s.kind in ("own", "shared")
                and _covered((s.t0, s.t1), rt)
                and abs(rt.t_end - s.t1) <= END_TOL_S
            ),
            None,
        )
        if seg is not None:
            e2e = turn.t - (res.t0 + seg.t1)
            m.e2e.append(e2e)
            m.pipeline.append(r.latency_s)
            m.endpoint.append(r.endpoint_s)
            m.compute.append(r.compute_s)
            m.wait.append(r.queue_wait_s)
            m.transport.append(e2e - r.latency_s)
        else:
            m.unmatched += 1
        m.replies += 1
        pub = res.pub_times.get(k + 1)
        if pub is not None:
            m.to_publish.append(pub - turn.t)
        if res.onsets:
            nxt = turns[k + 1].t if k + 1 < len(turns) else float("inf")
            on = next((o for o in res.onsets if turn.t <= o < min(nxt, turn.t + 3.0)), None)
            if on is None:
                m.unheard += 1
            else:
                m.to_heard.append(on - turn.t)


def _row(label: str, x: list[float]) -> str:
    return f"| {label} | {len(x)} | {ms(x, 50)} | {ms(x, 95)} |"


def render(fake: Metrics, lk: Metrics, header: str, wall_s: float) -> str:
    L = [SECTION, "", header + f" Wall time {wall_s:.0f}s.", ""]
    L += ["| path | n | p50 | p95 |", "|---|---|---|---|"]
    L += [
        _row("commit latency, FakeRoom (end of speech -> commit)", fake.e2e),
        _row("commit latency, LiveKit (end of speech -> commit)", lk.e2e),
    ]
    L += [
        "",
        "Commit latency is wall clock from the last speech sample leaving the publisher to "
        "`send_turn` (queue wait included), so the LiveKit row has the whole WebRTC path in it.",
        "",
        "Where the time goes (LiveKit; the per-turn pipeline latency is the stream-clock "
        "`endpoint + compute` from the room pipeline):",
        "",
        "| stage | n | p50 | p95 |",
        "|---|---|---|---|",
        _row("endpoint (segmenter hangover)", lk.endpoint),
        _row("compute (identify + queue wait)", lk.compute),
        _row("  of which queue wait", lk.wait),
        _row("transport-added (commit latency - pipeline latency, per turn)", lk.transport),
        _row("transport-added, FakeRoom (VAD end vs true end; the floor)", fake.transport),
        "",
    ]
    if lk.e2e and fake.e2e:
        d50 = 1000 * (np.percentile(lk.e2e, 50) - np.percentile(fake.e2e, 50))
        d95 = 1000 * (np.percentile(lk.e2e, 95) - np.percentile(fake.e2e, 95))
        L += [
            f"Transport-added delay, LiveKit minus FakeRoom: p50 {d50:+.0f} ms, p95 {d95:+.0f} ms.",
            "",
        ]
    L += ["Reply round trip with MockProvider (commit -> reply audio):", ""]
    L += ["| point | n | p50 | p95 |", "|---|---|---|---|"]
    L += [
        _row("FakeRoom: first audio published", fake.to_publish),
        _row("LiveKit: first audio published (handed to the SDK)", lk.to_publish),
        _row("LiveKit: first audio heard by a listener participant", lk.to_heard),
        "",
        f"LiveKit replies: {lk.replies} commits, {lk.unheard} not heard within 3 s "
        f"(cut by barge-in or the next turn); {lk.unmatched} commits (FakeRoom {fake.unmatched}) "
        "did not end at the end of an own/shared segment (split inside a segment, impostor, "
        "or ending more than 0.5 s from it) "
        "and are not in the latency rows.",
        "",
    ]
    if len(lk.e2e) < 20:
        L.append(f"**INSUFFICIENT DATA** (n={len(lk.e2e)} < 20 commits).")
    else:
        p95 = float(np.percentile(lk.e2e, 95))
        verdict = "MEETS" if p95 <= TARGET_S else "MISSES"
        L.append(
            f"LiveKit commit p95 {1000 * p95:.0f} ms vs {1000 * TARGET_S:.0f} ms: **{verdict}**."
        )
        if p95 >= TARGET_S:
            stages = {
                "endpoint": lk.endpoint,
                "compute": lk.compute,
                "transport": lk.transport,
            }
            p95s = {k: float(np.percentile(v, 95)) for k, v in stages.items()}
            var = max(("compute", "transport"), key=p95s.get)
            L.append(
                f"Stage p95: endpoint {1000 * p95s['endpoint']:.0f} ms (fixed hangover), "
                f"compute {1000 * p95s['compute']:.0f} ms, "
                f"transport {1000 * p95s['transport']:.0f} ms. "
                f"Dominant stage at p95: **{max(p95s, key=p95s.get)}**; largest variable stage: "
                f"**{var}**."
            )
    L.append("")
    return "\n".join(L)


def splice(report: str, section: str) -> str:
    """Replace (or append) the LiveKit section; other sections are kept."""
    if SECTION in report:
        head, _, rest = report.partition(SECTION)
        tail = rest.split("\n## ", 1)
        report = head.rstrip("\n") + ("\n\n## " + tail[1] if len(tail) > 1 else "")
    return report.rstrip("\n") + "\n\n" + section


async def settle() -> None:
    gc.collect()  # drop FFI handles while the loop still runs (D-018)
    await asyncio.sleep(0.2)


async def _run_all(server, work, emb, verify) -> tuple[list[ConvResult], list[ConvResult]]:
    """One conversation at a time (a room is one process's job; concurrent rooms in this loop
    starve the VAD), FakeRoom then LiveKit, so both see the same machine state."""
    fakes, lks = [], []
    for i, (cv, sg, cl, en) in enumerate(work):
        print(f"  conversation {i + 1}/{len(work)}", file=sys.stderr)
        fakes.append(await run_fake(cv, sg, cl, en, emb, verify))
        lks.append(await run_livekit(server, i, cv, sg, cl, en, emb, verify))
    await settle()
    return fakes, lks


def bench(corpus, emb, a, verify, out) -> int:
    from voiceroom.adapters.livekit_server import find_binary, run_dev_server

    if find_binary() is None:
        print("bench.room: livekit-server not found (scripts/fetch_livekit.py)", file=sys.stderr)
        return 2
    t0 = time.monotonic()
    n_conv = max(a.conversations, MIN_CONVERSATIONS)
    work = make_work(corpus, emb, n_conv, a.turns, a.seed, True)
    with run_dev_server() as server:
        fakes, lks = asyncio.run(_run_all(server, work, emb, verify))
    fm, lm = Metrics(), Metrics()
    for (conv, _, claims, _), f, k in zip(work, fakes, lks, strict=True):
        measure(conv, claims, f, fm)
        measure(conv, claims, k, lm)
    header = (
        f"{n_conv} bleed-variant conversations x {a.turns} turns (seed {a.seed}), each "
        "with 3 own-mic tracks + 1 shared mic, streamed in realtime, one at a time, "
        "through RoomAgent + SileroVAD + MockProvider into a local `livekit-server --dev` "
        "(localhost, no TURN), against the same run over FakeRoom."
    )
    section = render(fm, lm, header, time.monotonic() - t0)
    path = out / f"room-{emb.name}.md"
    base = path.read_text() if path.exists() else f"# Room benchmark: {emb.name}\n"
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(splice(base, section))
    print(path)
    return 0
