"""Tuned defaults chosen from the M0 bench (see .work/decisions.md D-009)."""

DEFAULT_EMBEDDER = "wespeaker-resnet34"

# Cosine-score operating point per embedder: clean-condition threshold with ~1 % overall FAR.
THRESHOLDS: dict[str, float] = {
    "wespeaker-resnet34": 0.35,
    "ecapa-voxceleb": 0.32,
    "campplus-en-voxceleb": 0.376,  # clean 1 % FAR point; candidate only (D-012)
}
FALLBACK_THRESHOLD = 0.35  # unknown embedders (e.g. toy); calibrate before trusting

# Speaker-continuity windows (policy.windows_ok): max dip of a ~0.5 s window below the turn's
# median owner score. Smaller = stricter. The toy value is calibrated by the e2e smoke (a
# >= 0.15 s foreign burst dips >= 0.014, natural variation <= 0.01); the real-embedder values
# come from `python -m voiceroom.bench.windows` on ~0.5 s windows (D-014) and unknown embedders
# get the strict toy one. At 0.5 s windows the check still falsely rejects many owner turns.
WINDOW_DROPS: dict[str, float] = {"wespeaker-resnet34": 0.4, "ecapa-voxceleb": 0.3}
FALLBACK_WINDOW_DROP = 0.012
# Consecutive bad windows (hop 0.25 s) that reject a turn. 0.5 s windows of one speaker are too
# noisy for "any bad window" (D-015); 3 in a row = a sustained span of >= ~1 s. Unknown
# embedders (toy) keep the strict 1.
WINDOW_BAD_RUNS: dict[str, int] = {"wespeaker-resnet34": 3}  # ecapa: not measured, strict
FALLBACK_WINDOW_BAD_RUN = 1

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
# The cohort file (data/cohort-<embedder>.npz, `voiceroom.cohort`) carries its own threshold and
# score_scale (z per cosine); ASNORM_THRESHOLDS is only the fallback for a bare cohort array.
# Policies multiply their cosine-scale buffers by the matcher's score_scale.
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


def window_bad_run_for(embedder_name: str) -> int:
    return WINDOW_BAD_RUNS.get(embedder_name, FALLBACK_WINDOW_BAD_RUN)


def window_drop_for(embedder_name: str) -> float:
    return WINDOW_DROPS.get(embedder_name, FALLBACK_WINDOW_DROP)
