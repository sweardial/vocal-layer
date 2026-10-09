"""Embedder registry. Optional backends are imported lazily on first use."""

from importlib import import_module

from voiceroom.embedders.base import Embedder, embed_batch

# name -> (module, class). Heavy backends are registered here by their own tasks.
_REGISTRY: dict[str, tuple[str, str]] = {
    "toy": ("voiceroom.embedders.toy", "ToyEmbedder"),
    "wespeaker-resnet34": ("voiceroom.embedders.wespeaker", "WespeakerEmbedder"),
    "campplus-en-voxceleb": ("voiceroom.embedders.campplus", "CamPlusEmbedder"),
    "ecapa-voxceleb": ("voiceroom.embedders.ecapa", "EcapaEmbedder"),
}


def available() -> list[str]:
    return sorted(_REGISTRY)


def get_embedder(name: str, **kwargs) -> Embedder:
    try:
        module, cls = _REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown embedder {name!r}; available: {available()}") from None
    try:
        mod = import_module(module)
    except ImportError as e:
        raise ImportError(f"embedder {name!r} needs optional dependencies: {e}") from e
    return getattr(mod, cls)(**kwargs)


__all__ = ["Embedder", "available", "embed_batch", "get_embedder"]
