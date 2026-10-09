"""Smoke: `voiceroom` CLI via subprocess (toy embedder, mock provider, synthetic wavs, no network).

enroll x2 -> list -> demo isolation on a conversation wav (only owner turns committed)
-> delete -> list empty.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from cryptography.fernet import Fernet

from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.eval.conversations import make_conversation, split_utterances
from voiceroom.eval.corpus import SynthCorpus
from voiceroom.matcher import Matcher

corpus = SynthCorpus(3, seed=1, utts_per_speaker=12)
spks = corpus.speakers()
emb = get_embedder("toy")
enroll_utts, held = split_utterances(corpus, spks, n_enroll=6)
enroll_audio = {s: np.concatenate([u.load() for u in enroll_utts[s]]) for s in spks}
names = {spks[0]: "alice", spks[1]: "bob"}  # the third speaker stays unenrolled

# Toy embedder: calibrate the threshold as the e2e smokes do (passed to the CLI explicitly).
sigs = [enroll(n, enroll_audio[s], emb) for s, n in names.items()]
cal = Matcher(sigs, threshold=0.0)
gen, imp = [], []
for s in spks:
    for u in held[s]:
        sc = cal.identify_embedding(emb.embed(u.load()[:16000])).scores
        gen += [sc[names[s]]] if s in names else []
        imp += [v for k, v in sc.items() if k != names.get(s)]
threshold = (float(np.percentile(gen, 5)) + float(np.percentile(imp, 95))) / 2
conv = make_conversation(
    corpus, spks, n_turns=6, pause_range=(0.6, 1.2), snr_db=None, seed=11, n_enroll=6
)

tmp = Path(tempfile.mkdtemp())
env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
env["VOICEROOM_KEY"] = Fernet.generate_key().decode()
env["VOICEROOM_STORE"] = str(tmp / "store")
exe = Path(sys.executable).parent / "voiceroom"
assert exe.exists(), "console script `voiceroom` missing: run `uv sync`"
base = [str(exe)]


def cli(*args, ok=True, stdin=None):
    p = subprocess.run(
        [*base, *args], capture_output=True, text=True, env=env, timeout=50, stdin=stdin
    )
    assert (p.returncode == 0) == ok, (args, p.returncode, p.stdout, p.stderr)
    return p


# Consent is mandatory: no --yes and no tty -> refuse, nothing stored.
wav = {}
for s, n in names.items():
    wav[n] = tmp / f"{n}.wav"
    sf.write(wav[n], enroll_audio[s], 16000)
refused = cli("enroll", "--name", "alice", "--file", str(wav["alice"]), "--embedder", "toy",
              ok=False, stdin=subprocess.DEVNULL)  # fmt: skip
assert "consent" in refused.stderr

for n, f in wav.items():
    cli("enroll", "--name", n, "--file", str(f), "--yes", "--embedder", "toy")
listed = cli("list").stdout
assert [ln.split("\t")[0] for ln in listed.splitlines()] == ["alice", "bob"], listed
assert not list((tmp / "store").glob("*alice*"))  # filenames don't reveal names

conv_wav = tmp / "conv.wav"
sf.write(conv_wav, conv.audio, 16000)
reply = tmp / "reply.wav"
TURN = re.compile(
    r"turn \d+ t=([\d.]+)-([\d.]+)s speaker=(\S+) decision=(\w+) latency=(\d+)ms"
)  # no error= suffix expected


def demo(mode, *extra):
    p = cli("demo", "--mode", mode, "--provider", "mock", "--input", str(conv_wav), "--out",
            str(reply), "--embedder", "toy", "--threshold", str(threshold), *extra)  # fmt: skip
    lines = p.stdout.splitlines()
    found = [TURN.fullmatch(ln) for ln in lines]
    assert all(found), p.stdout  # only turn lines: no audio, embeddings or other output
    turns = [(float(m[1]), float(m[2]), m[3], m[4]) for m in found]
    return turns


def truth(a, b):
    seg = max(conv.segments, key=lambda g: min(g[1], b) - max(g[0], a))
    return names.get(seg[2], "unknown")


turns = demo("isolation", "--owner", "alice")
committed = [t for t in turns if t[3] == "committed"]
owner_segs = [g for g in conv.segments if names.get(g[2]) == "alice"]
assert committed and all(truth(a, b) == "alice" for a, b, _, _ in committed), turns
assert len(committed) >= 0.8 * len(owner_segs), (len(committed), len(owner_segs))
assert reply.exists() and sf.info(reply).frames > 0  # the mock's reply tone was written

# Unreadable file / duplicate name fail cleanly, not with a traceback.
err = cli("enroll", "--name", "bob", "--yes", "--embedder", "toy", "--file", str(wav["bob"]),
          ok=False).stderr  # duplicate name  # fmt: skip
assert err.startswith("error:") and "Traceback" not in err, err

cli("delete", "--name", "alice")
cli("delete", "--name", "bob")
assert cli("list").stdout.strip() == ""
print("cli ok")
