"""Run every scripts/smoke/smoke_*.py in a subprocess; print PASS/FAIL/SKIP with timing.

A script exits 0 for PASS, 77 for SKIP, anything else is FAIL. Exits non-zero on any
failure or if the total time exceeds BUDGET_S.
"""

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BUDGET_S = 60.0  # wall time; scripts run WORKERS at a time (they were ~83 s serially, T-030)
CPUS = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
WORKERS = max(2, min(4, CPUS // 2))  # many smokes are realtime-paced (sleep-bound)
# Scripts run concurrently: one BLAS/torch thread each avoids oversubscribing the CPUs.
ENV = {
    **os.environ,
    **{k: "1" for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")},
}
SKIP_CODE = 77
PER_SCRIPT_TIMEOUT_S = 60.0


def run(path: Path) -> tuple[str, float, str]:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True,
            env=ENV,
            text=True,
            timeout=PER_SCRIPT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return "FAIL", time.monotonic() - start, "timed out"
    elapsed = time.monotonic() - start
    status = "PASS" if proc.returncode == 0 else "SKIP" if proc.returncode == SKIP_CODE else "FAIL"
    tail = "" if status == "PASS" else (proc.stdout + proc.stderr)[-2000:]
    return status, elapsed, tail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-j", type=int, default=WORKERS, help="concurrent scripts")
    args = parser.parse_args()
    scripts = sorted(Path(__file__).parent.glob("smoke_*.py"))
    total_start = time.monotonic()
    failed = 0
    with ThreadPoolExecutor(max(1, args.j)) as pool:
        results = list(pool.map(run, scripts))
    rows = sorted(zip(scripts, results, strict=True), key=lambda r: -r[1][1])
    for path, (status, elapsed, tail) in rows:
        print(f"{status} {path.name} ({elapsed:.2f}s)")
        if status == "FAIL":
            failed += 1
        if tail.strip():
            print(tail.rstrip())
    total = time.monotonic() - total_start
    print(f"{len(scripts)} scripts, {failed} failed, {total:.2f}s")
    if failed:
        return 1
    if total > BUDGET_S:
        print(f"FAIL: total {total:.1f}s exceeds {BUDGET_S:.0f}s budget")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
