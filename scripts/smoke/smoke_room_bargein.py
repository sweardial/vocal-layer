"""Smoke: barge-in in rooms (FakeRoom realtime, MockProvider with a 30 s reply, toy embedder).
A member speaking mid-reply interrupts once with a played_ms matching the wall clock; a stranger,
a bleed copy (no extra interrupt), a non-owner member in isolation, and the agent's own identity
interrupt nothing."""

import asyncio
import time

import numpy as np

from voiceroom.adapters import MockProvider
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.matcher import Matcher
from voiceroom.policy import IsolationPolicy, TrackPolicy
from voiceroom.room import RoomPipeline
from voiceroom.rooms import FakeParticipant, FakeRoom, RoomAgent
from voiceroom.synth import add_bleed
from voiceroom.vad import EnergyVAD

SR = 16000
corpus = SynthCorpus(4, seed=1, utts_per_speaker=12)
spks = corpus.speakers()
alice, bob, _carol, stranger = spks  # the stranger is never enrolled
members = [alice, bob]
emb = get_embedder("toy")
enroll_utts, held = split_utterances(corpus, spks, n_enroll=6)
sigs = [enroll(s, np.concatenate([u.load() for u in enroll_utts[s]]), emb) for s in members]
cal = Matcher(sigs, threshold=0.0)
gen, imp = [], []
for s in members:
    for u in held[s]:
        sc = cal.identify_embedding(emb.embed(u.load()[:SR])).scores
        gen.append(sc[s])
        imp += [v for k, v in sc.items() if k != s]
threshold = (float(np.percentile(gen, 5)) + float(np.percentile(imp, 95))) / 2
verify = threshold - 0.02
FIRST, SECOND = 0.5, 6.0  # utterance starts (s); the first one's reply plays through the second


def solo(parts: list[tuple[str, int, float]]) -> np.ndarray:
    end = max(t + 2.5 for _, _, t in parts) + 1.0
    audio = np.zeros(int(end * SR), np.float32)
    for s, i, t in parts:
        a = held[s][i].load()[: int(2.5 * SR)]
        audio[int(t * SR) : int(t * SR) + len(a)] = a
    return audio


class TimedProvider(MockProvider):
    def __init__(self) -> None:
        super().__init__(reply_s=30.0)
        self.interrupt_t: list[float] = []

    async def interrupt(self, played_ms=None) -> None:
        self.interrupt_t.append(time.monotonic())
        await super().interrupt(played_ms)


class TimedRoom(FakeRoom):
    first_pub: float | None = None
    t0 = 0.0

    async def connect(self) -> None:
        self.t0 = time.monotonic()
        await super().connect()

    async def publish_audio(self, audio) -> None:
        self.first_pub = self.first_pub or time.monotonic()
        await super().publish_audio(audio)


async def run(
    tracks: dict[str, tuple[np.ndarray, str]], isolation: bool = False, lenient: bool = False
):
    n = max(len(a) for a, _ in tracks.values())
    parts = [
        FakeParticipant(sid, np.pad(a, (0, n - len(a))), identity=ident)
        for sid, (a, ident) in tracks.items()
    ]
    room, provider = TimedRoom(parts, realtime=True), TimedProvider()
    if isolation:
        factory = lambda sid, claimed: IsolationPolicy(alice, threshold, embedder="toy")  # noqa: E731
    else:
        factory = lambda sid, claimed: TrackPolicy(  # noqa: E731
            "verified", members, claimed,
            threshold=threshold, verify_threshold=verify, window_drop=0.05,
            **({"on_mismatch": "relabel", "unknown": "label"} if lenient else {}),
        )  # fmt: skip
    pipeline = RoomPipeline(
        provider,
        factory,
        Matcher(sigs, threshold=threshold, embedder=emb),
        vad_factory=EnergyVAD,
        max_pending=1000,
        max_inflight_identify=1000,
        isolation="thread",
    )
    agent = RoomAgent(room, pipeline, provider)
    await agent.run()
    return room, provider, agent


def check_one(name, room, provider, n_turns):
    assert len(provider.interrupts) == 1, (name, provider.interrupts)
    assert len(provider.turns) == n_turns, (name, len(provider.turns))
    played = provider.interrupts[0]
    # the room's audio buffer was flushed and the reply stopped being published
    assert room.flushes == 1 and room.first_pub is not None, name
    wall = provider.interrupt_t[0] - room.first_pub
    assert played is not None and played > 1000 and abs(played / 1000 - wall) < 0.3, (
        name,
        played,
        wall,
    )
    assert played < 30000 and room.clock.cancelled == 1, (name, played, room.clock.cancelled)


def check_none(name, room, provider, n_turns=1):
    assert provider.interrupts == [] and room.flushes == 0, (name, provider.interrupts)
    assert len(provider.turns) == n_turns, (name, len(provider.turns))


async def main() -> None:
    a1 = solo([(alice, 0, FIRST)])
    member = {"a": (a1, alice), "b": (solo([(bob, 0, SECOND)]), bob)}
    a2 = {"a": (solo([(alice, 0, FIRST), (alice, 1, SECOND)]), alice)}
    bl = np.zeros(len(a2["a"][0]), np.float32)
    bleed = {**a2, "b": (add_bleed(bl, a2["a"][0], -12.0, 15.0), bob)}
    stran = {"a": (a1, alice), "b": (solo([(stranger, 0, SECOND)]), bob)}
    iso_member = {"a": (a1, alice), "b": (solo([(bob, 0, SECOND)]), bob)}
    own = {"a": (a1, alice), "bot": (solo([(alice, 1, SECOND)]), "voiceroom-bot")}
    res = await asyncio.gather(
        run(member),
        run(bleed, lenient=True),
        run(stran),
        run(iso_member, True),
        run(a2, True),
        run(own),
    )
    check_one("member", *res[0][:2], 2)
    # the bleed copy adds no turn and no second interrupt: only Alice's own speech interrupts
    check_one("bleed", *res[1][:2], 2)
    check_none("stranger", *res[2][:2])
    check_none("isolation non-owner", *res[3][:2])
    check_one("isolation owner", *res[4][:2], 2)  # control: the owner does interrupt
    check_none("own audio", *res[5][:2])
    assert "bot" not in res[5][2].room.sources and "bot" not in res[5][2].participants
    print("room bargein ok")


asyncio.run(main())
