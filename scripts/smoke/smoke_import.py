"""Smoke: the package imports and exposes a version."""

import voiceroom

assert isinstance(voiceroom.__version__, str) and voiceroom.__version__
print(f"voiceroom {voiceroom.__version__}")
