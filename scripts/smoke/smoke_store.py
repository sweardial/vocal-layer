"""Smoke: encrypted signature store round-trip, no plaintext leaks, wrong key, delete, key mode."""

import os
import stat
import tempfile
import threading
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet

from voiceroom import synth
from voiceroom.embedders import get_embedder
from voiceroom.enroll import enroll
from voiceroom.store import KEY_ENV, SignatureStore, StoreError, StoreKeyError, load_key

os.environ.pop(KEY_ENV, None)
sig = enroll("Zebediah", synth.utterance(1, 24.0, seed=7), get_embedder("toy"))

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    keyfile = d / "k" / "key"
    key = load_key(keyfile)
    assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600, oct(keyfile.stat().st_mode)
    assert load_key(keyfile) == key  # stable on reload
    assert stat.S_IMODE(keyfile.parent.stat().st_mode) == 0o700
    keyfile.chmod(0o644)
    try:
        load_key(keyfile)
        raise SystemExit("expected StoreKeyError for group/world-readable keyfile")
    except StoreKeyError as e:
        print("insecure keyfile ->", e)
    keyfile.chmod(0o600)
    empty = d / "empty" / "key"
    empty.parent.mkdir()
    empty.touch(mode=0o600)
    try:
        load_key(empty)
        raise SystemExit("expected StoreKeyError for empty keyfile")
    except StoreKeyError:
        assert empty.read_bytes() == b""  # not silently regenerated
    racers = [d / "race" / "key"] * 8
    out: list[bytes] = []
    ts = [threading.Thread(target=lambda p=p: out.append(load_key(p))) for p in racers]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(out) == 8 and len(set(out)) == 1, "concurrent first-time key init diverged"
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

    errs: list[BaseException] = []

    def _save(i: int) -> None:
        try:
            for _ in range(10):
                store.save(sig, consent={"n": "x" * (1 + i * 500)})
        except BaseException as e:
            errs.append(e)

    ts = [threading.Thread(target=_save, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    assert store.list() == ["Zebediah"] and store.load("Zebediah").name == "Zebediah"
    assert [f for f in (d / "store").iterdir() if f.suffix != ".sig"] == []

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
    # list() with the wrong key raises; a swapped file is rejected by load().
    try:
        SignatureStore(d / "store", Fernet.generate_key()).list()
        raise SystemExit("expected StoreKeyError from list with wrong key")
    except StoreKeyError:
        pass
    other = enroll("Mallory", synth.utterance(2, 24.0, seed=8), get_embedder("toy"))
    store.save(other)
    (d / "store" / store._path("Zebediah").name).write_bytes(store._path("Mallory").read_bytes())
    try:
        store.load("Zebediah")
        raise SystemExit("expected StoreError for swapped file")
    except StoreError:
        pass
    store._path("Mallory").unlink()
    store.save(sig)
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
