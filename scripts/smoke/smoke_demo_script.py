"""Smoke: scripts/live/run_macos_demo.sh with stubbed commands (no network, no audio).

A .env that sets VOICEROOM_STORE to a "personal" store must not redirect the demo: every voiceroom
subcommand gets an explicit --store pointing at the isolated demo store, and cleanup leaves the
personal store untouched.
"""

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/live/run_macos_demo.sh"

STDIN = "hp\ny\n\n\n" + "a\n" * 6 + "\n\ny\nnone\n"


def run(d: Path, bin_: Path, out: Path, args: list[str], stdin: str = STDIN):
    env = {
        **os.environ,
        "PATH": f"{bin_}:{os.environ['PATH']}",
        "VOICEROOM_ENV_FILE": str(d / "env"),
        "VOICEROOM_DEMO_OUT": str(out),
        "TMPDIR": str(d),
    }
    env.pop("VOICEROOM_STORE", None)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
        cwd=ROOT,
    )


with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    bin_ = d / "bin"
    bin_.mkdir()
    calls = d / "calls.txt"
    out = d / "out"
    out.mkdir()
    personal = d / "personal-store"
    personal.mkdir()
    (personal / "alice.sig").write_text("precious")
    (d / "env").write_text(f"OPENAI_API_KEY=sk-fake\nVOICEROOM_STORE={personal}\n")
    (out / "hello.wav").write_text("preexisting")
    stubs = {
        "uname": "echo Darwin",
        "sw_vers": "echo 27.0",
        "uv": f"""echo "uv $*" >> {calls}
case "$*" in
  *openai_realtime_check*)
    [ -e "{d}/fail" ] && exit 1
    while [ $# -gt 0 ]; do [ "$1" = --out ] && echo reply > "$2"; shift; done
    echo "first audio after 0.5s"; exit 0;;
  "run python -V"*) echo "Python 3.12";;
esac
exit 0""",
        "say": 'while [ $# -gt 0 ]; do [ "$1" = -o ] && echo wav > "$2"; shift; done',
        "afplay": f'echo "afplay $*" >> {calls}',
    }
    for name, body in stubs.items():
        f = bin_ / name
        f.write_text("#!/bin/sh\n" + body + "\n")
        f.chmod(0o755)

    def untouched():
        assert (personal / "alice.sig").read_text() == "precious"
        assert (out / "hello.wav").read_text() == "preexisting"

    # Redirected stores recorded by a previous run must be refused before any command runs.
    rec = out / "demo-store.txt"
    link = d / "vr-demo.link"
    link.symlink_to(personal)
    for bad in (str(link), str(d / "vr-demo.x" / ".." / "personal-store"), str(personal)):
        rec.write_text(bad + "\n")
        calls.write_text("")
        r = run(d, bin_, out, ["--demo-only"])
        assert r.returncode != 0, bad
        assert "voiceroom" not in calls.read_text(), bad
        untouched()

    # A genuine private demo store is used with an explicit --store, then removed.
    store = d / "vr-demo.ok"
    store.mkdir()
    (store / "x").write_text("demo")
    rec.write_text(str(store) + "\n")
    calls.write_text("")
    r = run(d, bin_, out, ["--demo-only"])
    assert r.returncode == 0, r.stdout + r.stderr
    subs = [
        ln for ln in calls.read_text().splitlines() if " voiceroom " in ln and "--help" not in ln
    ]
    assert any(" demo " in ln for ln in subs) and any(" delete " in ln for ln in subs), subs
    real = os.path.realpath(store)
    for ln in subs:
        assert f"--store {real}" in ln and str(personal) not in ln, ln
    assert not store.exists() and not rec.exists()
    untouched()
    # Full run (stubbed audio + API): audio goes to a fresh temp dir, never the checkout or $OUT.
    ok_in = "hp\ny\n\ny\n\n\ny\n\n" + "a\n" * 6 + "\ny\nnone\n"
    fail_in = "hp\ny\n\ny\n\n\n\ny\n\n" + "a\n" * 6 + "\ny\nnone\n"  # extra y: continue anyway
    for fail in (False, True):
        (d / "fail").unlink(missing_ok=True)
        full_in = ok_in
        if fail:
            (d / "fail").write_text("")
            full_in = fail_in
        calls.write_text("")
        r = run(d, bin_, out, [], full_in)
        assert r.returncode == 0, r.stdout + r.stderr
        text = calls.read_text()
        assert "data/out" not in text and "data/hello" not in text, text
        assert ("afplay" in text) != fail, text  # a failed check must not play any reply
        assert not [x for x in d.glob("vr-demo*") if x.name != "vr-demo.link"], (
            "run-owned temp dirs should be removed"
        )
        untouched()
print("OK demo script isolates its store")
