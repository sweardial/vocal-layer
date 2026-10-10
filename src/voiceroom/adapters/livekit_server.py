"""Local `livekit-server --dev` for offline tests: free ports, readiness wait, always killed.

The binary comes from scripts/fetch_livekit.py (data/bin/), $LIVEKIT_SERVER_BIN, or PATH.
"""

import contextlib
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

DEV_KEY = "devkey"  # the --dev defaults
DEV_SECRET = "secret"
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class LiveKitServer:
    url: str  # ws://127.0.0.1:<port>
    api_key: str = DEV_KEY
    api_secret: str = DEV_SECRET


def find_binary() -> Path | None:
    for cand in (os.environ.get("LIVEKIT_SERVER_BIN"), _ROOT / "data/bin/livekit-server"):
        if cand and Path(cand).is_file():
            return Path(cand)
    found = shutil.which("livekit-server")
    return Path(found) if found else None


def _free_ports(n: int) -> list[int]:
    socks = [socket.socket() for _ in range(n)]
    try:
        for s in socks:
            s.bind(("127.0.0.1", 0))
        return [s.getsockname()[1] for s in socks]
    finally:
        for s in socks:
            s.close()


@contextlib.contextmanager
def run_dev_server(
    binary: Path | None = None, ready_timeout_s: float = 8.0
) -> Iterator[LiveKitServer]:
    """Start the dev server on free localhost ports (signal, ICE/TCP, ICE/UDP); kill it on exit."""
    binary = binary or find_binary()
    if binary is None:
        raise FileNotFoundError("livekit-server not found; run scripts/fetch_livekit.py")
    port, tcp_port, udp_port = _free_ports(3)
    config = (
        f"port: {port}\n"
        "rtc:\n"
        f"  tcp_port: {tcp_port}\n"
        f"  udp_port: {udp_port}\n"
        "  use_external_ip: false\n"
        '  stun_servers: ["127.0.0.1:1"]\n'  # default is Google STUN; keep checks offline
    )
    cmd = [str(binary), "--dev", "--bind", "127.0.0.1", "--node-ip", "127.0.0.1"]
    cmd += ["--config-body", config]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
    try:
        deadline = time.monotonic() + ready_timeout_s
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"livekit-server exited early (code {proc.returncode})")
            try:
                with _NO_PROXY.open(f"http://127.0.0.1:{port}/", timeout=0.5):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("livekit-server not ready") from None
                time.sleep(0.05)
        yield LiveKitServer(url=f"ws://127.0.0.1:{port}")
    finally:
        proc.kill()
        proc.wait()
