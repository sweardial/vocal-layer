"""Tuned defaults chosen from the M0 bench (see .work/decisions.md D-009)."""

DEFAULT_EMBEDDER = "wespeaker-resnet34"

# Cosine-score operating point per embedder: clean-condition threshold with ~1 % overall FAR.
THRESHOLDS: dict[str, float] = {
    "wespeaker-resnet34": 0.35,
    "ecapa-voxceleb": 0.32,
}
FALLBACK_THRESHOLD = 0.35  # unknown embedders (e.g. toy); calibrate before trusting

# Speaker-continuity windows (policy.windows_ok): max dip of a ~0.5 s window below the turn's
# median owner score. Smaller = stricter. The toy value is calibrated by the e2e smoke (a
# >= 0.15 s foreign burst dips >= 0.014, natural variation <= 0.01); the real-embedder values
# are PROVISIONAL (not yet calibrated on bench audio) and unknown embedders get the strict one.
WINDOW_DROPS: dict[str, float] = {"wespeaker-resnet34": 0.05, "ecapa-voxceleb": 0.05}
FALLBACK_WINDOW_DROP = 0.012

# Matcher: runner-up margin (0 = off; not needed at the bench operating point).
MIN_MARGIN = 0.0

# Enrollment: speech needed and the chunking used for the signature's chunk embeddings.
MIN_SPEECH_S = 10.0
WINDOW_S = 3.0
HOP_S = 1.5

# Noise-augmented enrollment: extra noisy copy of each window per SNR in dB (D-011). () = off.
ENROLL_NOISE_SNRS: tuple[float, ...] = ()

# AS-norm (D-011): needs a cohort of impostor embeddings passed to Matcher(cohort=...); normalized
# scores live on a different scale (z-units), so they have their own thresholds (FAR ~1 %),
# valid only for a bench-like cohort (~220 windows of other speakers, top-N 100); recalibrate
# for any other cohort.
# IsolationPolicy's early buffers are cosine-scale: rescale them before enabling AS-norm there.
ASNORM_THRESHOLDS: dict[str, float] = {"wespeaker-resnet34": 2.88}
ASNORM_TOP_N = 100

# Early decision: shortest segment (s) the pipeline should classify. Consumed by the pipeline
# (M1); shorter segments have ~19 % clean FRR at the threshold above.
MIN_DECISION_S = 1.0


def threshold_for(embedder_name: str) -> float:
    return THRESHOLDS.get(embedder_name, FALLBACK_THRESHOLD)


def asnorm_threshold_for(embedder_name: str) -> float:
    try:
        return ASNORM_THRESHOLDS[embedder_name]
    except KeyError:
        raise ValueError(f"no AS-norm threshold calibrated for {embedder_name!r}") from None


def window_drop_for(embedder_name: str) -> float:
    return WINDOW_DROPS.get(embedder_name, FALLBACK_WINDOW_DROP)
