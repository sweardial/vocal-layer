"""Smoke: encrypted signature store round-trip, no plaintext leaks, wrong key, delete, key mode."""

import os
import stat
import tempfile
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet

from voiceroom import synth
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.store import KEY_ENV, SignatureStore, StoreKeyError, load_key

os.environ.pop(KEY_ENV, None)
sig = enroll("Zebediah", synth.utterance(1, 24.0, seed=7), get_embedder("toy"))

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    keyfile = d / "k" / "key"
    key = load_key(keyfile)
    assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600, oct(keyfile.stat().st_mode)
    assert load_key(keyfile) == key  # stable on reload
    os.environ[KEY_ENV] = Fernet.generate_key().decode()
    assert load_key(keyfile) == os.environ[KEY_ENV].encode()  # env wins
    del os.environ[KEY_ENV]

    store = SignatureStore(d / "store", key)
    store.save(sig, consent={"by": "zeb", "scope": "demo"})
    got = store.load("Zebediah")
    assert got.name == sig.name and got.embedder == sig.embedder
    assert np.array_equal(got.centroid, sig.centroid) and np.array_equal(got.chunks, sig.chunks)
    assert store.metadata("Zebediah")["consent"]["by"] == "zeb"
    assert store.list() == ["Zebediah"]

    files = list((d / "store").iterdir())
    assert len(files) == 1
    raw = files[0].read_bytes()
    assert b"Zebediah" not in raw and b"Zebediah" not in files[0].name.encode()
    assert sig.centroid.tobytes() not in raw and sig.chunks.tobytes() not in raw
    assert str(sig.centroid[0]) not in repr(store) + repr(got)

    try:
        SignatureStore(d / "store", Fernet.generate_key()).load("Zebediah")
        raise SystemExit("expected StoreKeyError for wrong key")
    except StoreKeyError as e:
        print("wrong key ->", e)
    try:
        SignatureStore(d / "store", b"nope")
        raise SystemExit("expected StoreKeyError for malformed key")
    except StoreKeyError:
        pass

    assert store.delete("Zebediah") and not store.delete("Zebediah")
    assert not files[0].exists() and store.list() == []
    try:
        store.load("Zebediah")
        raise SystemExit("expected KeyError after delete")
    except KeyError:
        pass
print("ok")
