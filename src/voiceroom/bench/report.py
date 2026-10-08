"""Write reports/bench-<embedder>.md."""

from pathlib import Path

import numpy as np

from voiceroom.bench import metrics as M
from voiceroom.bench.run import Trials


def _pct(x: float) -> str:
    return "n/a" if np.isnan(x) else f"{100 * x:.2f}%"


def _num(x: float, fmt: str = ".3f") -> str:
    return "n/a" if np.isnan(x) else format(x, fmt)


def write_report(
    out_dir: str | Path,
    embedder_name: str,
    trials: Trials,
    header: dict[str, object],
    threshold: float | None = None,
    target_fars: tuple[float, ...] = (0.01, 0.001),
) -> Path:
    """`threshold` is the operating point for FAR/FRR; default is the EER threshold."""
    s, y = trials.scores, trials.labels
    e, e_thr = M.eer(s, y)
    thr = e_thr if threshold is None else threshold
    far, frr = M.far_frr(s, y, thr)
    lines = [f"# Speaker-ID bench: {embedder_name}", ""]
    lines += [f"- **{k}**: {v}" for k, v in header.items()]
    lines += [
        f"- **trials**: {int(y.sum())} target, {int((~y).sum())} non-target",
        "",
        "## EER",
        "",
        f"EER **{_pct(e)}** at threshold {_num(e_thr)}.",
        "",
        "## FAR / FRR at the operating threshold",
        "",
        f"Threshold {_num(thr)}"
        + (" (EER threshold)" if threshold is None else " (configured)")
        + f": FAR {_pct(far)}, FRR {_pct(frr)}.",
        "",
        "Threshold for a target FAR:",
        "",
        "| target FAR | threshold | actual FAR | FRR |",
        "|---|---|---|---|",
    ]
    for t in target_fars:
        try:
            th = M.threshold_for_far(s, y, t)
            f_, r_ = M.far_frr(s, y, th)
            lines.append(f"| {_pct(t)} | {_num(th)} | {_pct(f_)} | {_pct(r_)} |")
        except ValueError:
            lines.append(f"| {_pct(t)} | n/a | n/a | n/a |")
    lines += [
        "",
        "## Results by utterance length",
        "",
        f"FAR/FRR at threshold {_num(thr)}.",
        "",
        "| length | targets | non-targets | EER | FAR | FRR |",
        "|---|---|---|---|---|---|",
    ]
    for name, b in M.by_length(s, y, trials.durations, thr).items():
        lines.append(
            f"| {name} | {b['n_target']} | {b['n_nontarget']} | {_pct(b['eer'])} "
            f"| {_pct(b['far'])} | {_pct(b['frr'])} |"
        )
    long = trials.durations >= 1.0
    far1, frr1 = M.far_frr(s[long], y[long], thr)
    lines.insert(
        len(lines) - 0,
        f"| all >=1 s | {int(y[long].sum())} | {int((~y[long]).sum())} "
        f"| {_pct(M.eer(s[long], y[long])[0])} | {_pct(far1)} | {_pct(frr1)} |",
    )
    st = M.latency_stats(trials.latencies)
    lines += [
        "",
        "## Latency (per-clip embedding)",
        "",
        f"- segments: {len(trials.latencies)}",
        f"- p50: {_num(st['p50_ms'], '.1f')} ms",
        f"- p95: {_num(st['p95_ms'], '.1f')} ms",
        "",
        "## Real-time factor",
        "",
        f"RTF {_num(M.rtf(trials.latencies, trials.segment_durations), '.4f')} "
        "(embedding time / audio duration; < 1 is faster than real time).",
        "",
    ]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"bench-{embedder_name}.md"
    path.write_text("\n".join(lines))
    return path
