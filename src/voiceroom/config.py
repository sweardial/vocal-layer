"""Tuned defaults chosen from the M0 bench (see .work/decisions.md D-009)."""

DEFAULT_EMBEDDER = "wespeaker-resnet34"

# Cosine-score operating point per embedder: clean-condition threshold with ~1 % overall FAR.
THRESHOLDS: dict[str, float] = {
    "wespeaker-resnet34": 0.35,
    "ecapa-voxceleb": 0.32,
}
FALLBACK_THRESHOLD = 0.35  # unknown embedders (e.g. toy); calibrate before trusting

# Matcher: runner-up margin (0 = off; not needed at the bench operating point).
MIN_MARGIN = 0.0

# Enrollment: speech needed and the chunking used for the signature's chunk embeddings.
MIN_SPEECH_S = 10.0
WINDOW_S = 3.0
HOP_S = 1.5

# Early decision: shortest segment (s) the pipeline should classify. Consumed by the pipeline
# (M1); shorter segments have ~19 % clean FRR at the threshold above.
MIN_DECISION_S = 1.0


def threshold_for(embedder_name: str) -> float:
    return THRESHOLDS.get(embedder_name, FALLBACK_THRESHOLD)
