"""Smoke: `voiceroom session|token|room` via subprocess (toy embedder, mock provider, no network).

enroll x3 -> session create (collaboration) -> invite/accept bob -> room on a scripted 3-participant
FakeRoom (alice and bob committed, carol dropped) -> revoke bob -> room again (bob never committed).
With the livekit extra and server binary, also `room --transport livekit` on a short conversation.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from cryptography.fernet import Fernet

from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.matcher import Matcher

SR = 16000
corpus = SynthCorpus(3, seed=1, utts_per_speaker=12)
spks = corpus.speakers()
names = dict(zip(spks, ["alice", "bob", "carol"], strict=True))
emb = get_embedder("toy")
enroll_utts, held = split_utterances(corpus, spks, n_enroll=6)
enroll_audio = {s: np.concatenate([u.load() for u in enroll_utts[s]]) for s in spks}
sigs = [enroll(n, enroll_audio[s], emb) for s, n in names.items()]
cal = Matcher(sigs, threshold=0.0)
gen, imp = [], []
for s in spks:
    for u in held[s]:
        sc = cal.identify_embedding(emb.embed(u.load()[:SR])).scores
        gen.append(sc[names[s]])
        imp += [v for k, v in sc.items() if k != names[s]]
threshold = (float(np.percentile(gen, 5)) + float(np.percentile(imp, 95))) / 2


def track(spk: str, idxs: list[int]) -> np.ndarray:
    parts = []
    for i in idxs:
        parts += [np.zeros(int(0.5 * SR), np.float32), held[spk][i].load()[: int(2.0 * SR)]]
    return np.concatenate(parts + [np.zeros(SR, np.float32)]).astype(np.float32)


tmp = Path(tempfile.mkdtemp())
env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "LIVEKIT_URL")}
env.update(OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")  # leave CPU to the parallel smokes
env["VOICEROOM_KEY"] = Fernet.generate_key().decode()
env["VOICEROOM_STORE"] = str(tmp / "store")
exe = Path(sys.executable).parent / "voiceroom"
assert exe.exists(), "console script `voiceroom` missing: run `uv sync`"
TUNE = ["--embedder", "toy", "--no-cohort", "--threshold", str(threshold),
        "--verify-threshold", str(threshold - 0.02), "--window-drop", "0.05",
        "--vad", "energy", "--provider", "mock"]  # fmt: skip


def cli(*args, ok=True):
    p = subprocess.run([str(exe), *args], capture_output=True, text=True, env=env, timeout=50)
    assert (p.returncode == 0) == ok, (args[:2], p.returncode, p.stdout, p.stderr[-800:])
    return p


for s, n in names.items():
    f = tmp / f"enroll-{n}.wav"
    sf.write(f, enroll_audio[s], SR)
    cli("enroll", "--name", n, "--file", str(f), "--yes", "--embedder", "toy")

created = cli("session", "create", "--owner", "alice", "--mode", "collaboration").stdout.split()
sid, room = created
assert cli("session", "list").stdout.split("\t")[0] == sid
code = cli("session", "invite", "--session", sid, "--user", "bob").stdout.strip()
assert code.startswith("vr1.")
assert "joined" in cli("session", "accept", "--code", code).stdout
err = cli("session", "accept", "--code", code, ok=False).stderr  # single use
assert err.startswith("error:") and code not in err, err
cli("session", "invite", "--session", "nope", "--user", "bob", ok=False)

script = {"participants": []}
for s, n in names.items():
    sf.write(tmp / f"{n}.wav", track(s, [0, 1]), SR)
    script["participants"].append({"id": f"p-{n}", "wav": f"{n}.wav", "identity": n})
(tmp / "script.json").write_text(json.dumps(script))

LINE = re.compile(
    r"turn \d+ t=[\d.]+-[\d.]+s source=(\S+) claimed=(\S+) decision=(committed|dropped) "
    r"label=(\S+) latency=\d+ms(?: error=\S+)?(?: bleed_of=\S+)?"
)


def run_room(*extra):
    p = cli("room", "--session", sid, "--transport", "fake", "--script", str(tmp / "script.json"),
            *TUNE, *extra)  # fmt: skip
    found = [LINE.fullmatch(ln) for ln in p.stdout.splitlines()]
    assert all(found), p.stdout  # only turn lines: no audio, embeddings, tokens or codes
    assert code not in p.stdout + p.stderr
    return [m.groups() for m in found]


def committed(turns, who):
    return [t for t in turns if t[1] == who and t[2] == "committed"]


turns = run_room()
assert len(committed(turns, "alice")) >= 1 and len(committed(turns, "bob")) >= 1, turns
assert all(t[3] == t[1] for t in committed(turns, "alice") + committed(turns, "bob")), turns
assert not committed(turns, "carol") and any(t[1] == "carol" for t in turns), turns

cli("session", "revoke", "--session", sid, "--user", "bob")
turns = run_room()
assert committed(turns, "alice") and not committed(turns, "bob"), turns
assert not committed(turns, "carol"), turns
print("cli room (fake) ok:", len(turns), "turns after revoke")

# ---- LiveKit (optional) ----------------------------------------------------------------
SKIP_MSG = None
try:
    from livekit import rtc

    from voiceroom.adapters.livekit_server import find_binary, run_dev_server
except ImportError:
    SKIP_MSG = "`livekit` extra not installed"
else:
    if find_binary() is None:
        SKIP_MSG = "livekit-server not found (scripts/fetch_livekit.py)"
if SKIP_MSG:
    print("cli room (livekit) skipped:", SKIP_MSG)
    sys.exit(0)

bob_code = cli("session", "invite", "--session", sid, "--user", "bob").stdout.strip()
cli("session", "accept", "--code", bob_code)


async def publish(url: str, token: str, audio: np.ndarray) -> tuple[rtc.Room, rtc.AudioSource]:
    r = rtc.Room()
    await r.connect(url, token)
    source = rtc.AudioSource(SR, 1)
    mic = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await r.local_participant.publish_track(
        mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    n = SR // 100
    for i in range(0, len(pcm) - n + 1, n):
        await source.capture_frame(rtc.AudioFrame(pcm[i : i + n].tobytes(), SR, 1, n))
    return r, source


async def live(url: str, proc: subprocess.Popen, toks: dict) -> None:
    watcher = rtc.Room()
    await watcher.connect(url, cli("token", "--session", sid, "--user", "alice", "--url", url)
                          .stdout.strip())  # fmt: skip
    t_end = time.monotonic() + 20
    while not any(p.identity == "voiceroom-bot" for p in watcher.remote_participants.values()):
        assert time.monotonic() < t_end and proc.poll() is None, "bot never joined"
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)
    pubs = await asyncio.gather(*(publish(url, toks[n], track(s, [0])) for s, n in names.items()
                                  if n in toks))  # fmt: skip
    await asyncio.sleep(1.0)
    for r, src in pubs:
        await src.aclose()
        await r.disconnect()
    await watcher.disconnect()


t0 = time.monotonic()
with run_dev_server() as server:
    toks = {n: cli("token", "--session", sid, "--user", n, "--url", server.url).stdout.strip()
            for n in ("alice", "bob")}  # fmt: skip
    proc = subprocess.Popen(
        [str(exe), "room", "--session", sid, "--transport", "livekit", "--url", server.url,
         "--duration", "12", *TUNE],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )  # fmt: skip
    try:
        asyncio.run(live(server.url, proc, toks))
        out, err = proc.communicate(timeout=40)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise AssertionError(f"room agent did not stop: {proc.communicate()[1][-800:]}") from None
    finally:
        proc.kill()
assert proc.returncode == 0, (proc.returncode, err[-800:])
found = [LINE.fullmatch(ln) for ln in out.splitlines()]
assert all(found), out
lk = [m.groups() for m in found]
assert committed(lk, "alice") and committed(lk, "bob"), lk
assert not any(t in out + err for t in (bob_code,)), "secret leaked"
print(f"cli room (livekit) ok: {len(lk)} turns, {time.monotonic() - t0:.1f}s")
