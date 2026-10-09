"""`voiceroom` command line: enroll, list, delete, demo. Never prints audio or embeddings."""

import argparse
import asyncio
import contextlib
import functools
import os
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

from voiceroom.audio import SAMPLE_RATE, load_audio
from voiceroom.cohort import Cohort, load_default
from voiceroom.config import DEFAULT_EMBEDDER, threshold_for
from voiceroom.enroll import EnrollmentError, Signature, enroll
from voiceroom.matcher import Matcher
from voiceroom.policy import UNKNOWN_LABEL
from voiceroom.store import SignatureStore, StoreError, load_key

STORE_ENV = "VOICEROOM_STORE"
DEFAULT_STORE = Path.home() / ".voiceroom" / "signatures"
ENROLL_SECONDS = 25.0
OPENAI_LINGER_S = 5.0  # file input ends abruptly; let the model's reply finish playing


class CliError(Exception):
    pass


def _store(args: argparse.Namespace) -> SignatureStore:
    root = args.store or os.environ.get(STORE_ENV) or DEFAULT_STORE
    return SignatureStore(root, load_key(args.keyfile))


def _device(s: str | None) -> int | str | None:
    return None if s is None else int(s) if s.isdigit() else s


def _embedder(name: str):
    from voiceroom.embedders import get_embedder

    try:
        return get_embedder(name)
    except (ValueError, ImportError, OSError) as e:
        raise CliError(str(e)) from e


# ---- enroll -----------------------------------------------------------------------------


def _consent(args: argparse.Namespace) -> dict:
    if args.yes:
        return {"given": True, "method": "flag", "at": time.time()}
    if not sys.stdin.isatty():
        raise CliError("consent required: run interactively or pass --yes")
    print(
        f"A voice signature (numbers derived from your voice, not the audio) will be stored\n"
        f"encrypted on this machine for '{args.name}'. You can delete it any time with\n"
        f"`voiceroom delete --name {args.name}`."
    )
    if input("Do you consent? [y/N] ").strip().lower() not in ("y", "yes"):
        raise CliError("consent not given; nothing recorded")
    return {"given": True, "method": "prompt", "at": time.time()}


async def _record(seconds: float, device: int | str | None) -> np.ndarray:
    from voiceroom.io import MicSource

    frames, n = [], int(seconds * SAMPLE_RATE)
    got = 0
    async with MicSource(device) as mic:
        async for f in mic:
            frames.append(f)
            got += len(f)
            if got >= n:
                break
            if len(frames) % 31 == 0:  # ~1 s
                print(f"\r  {got / SAMPLE_RATE:4.0f} / {seconds:.0f} s", end="", flush=True)
    print()
    return np.concatenate(frames)


def cmd_enroll(args: argparse.Namespace) -> int:
    if not args.name.strip():
        raise CliError("--name must not be empty")
    if args.name == UNKNOWN_LABEL:
        raise CliError(f"{UNKNOWN_LABEL!r} is reserved; pick another name")
    store = _store(args)
    if args.name in store.list() and not args.force:
        raise CliError(f"{args.name!r} is already enrolled; pass --force to replace it")
    audio = None
    if args.file:
        try:
            audio = load_audio(args.file)
        except (RuntimeError, OSError) as e:
            raise CliError(f"cannot read {args.file}: {e}") from e
    consent = _consent(args)
    emb = _embedder(args.embedder)
    if audio is None:
        print(f"Recording {args.seconds:.0f} s from the microphone. Speak naturally, starting now.")
        try:
            audio = asyncio.run(_record(args.seconds, _device(args.input_device)))
        except RuntimeError as e:  # e.g. sounddevice/PortAudio missing
            raise CliError(f"cannot record: {e}") from e
        except KeyboardInterrupt:
            raise CliError("cancelled; nothing recorded") from None
    try:
        sig = enroll(args.name, audio, emb)
    except EnrollmentError as e:
        raise CliError(f"enrollment failed: {e}") from e
    store.save(sig, consent)
    print(f"enrolled {sig.name} ({sig.embedder}, {sig.speech_seconds:.0f} s of speech)")
    return 0


# ---- list / delete ----------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    store = _store(args)
    for name in store.list():
        meta = store.metadata(name)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(meta["saved_at"]))
        print(f"{name}\t{meta['embedder']}\t{when}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    if _store(args).delete(args.name):
        print(f"deleted {args.name}")
        return 0
    raise CliError(f"no signature named {args.name!r}")


# ---- demo -------------------------------------------------------------------------------


def _build_matcher(
    embedder: str, sigs: list[Signature], threshold: float, cohort: Cohort | None = None
) -> Matcher:
    """Module-level so the identification child process can build it (ONNX isn't picklable)."""
    return Matcher(sigs, threshold=threshold, embedder=_embedder(embedder), cohort=cohort)


def _warn_playback(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        print("warning: audio playback failed", file=sys.stderr)


class _PlayingProvider:
    """Wraps a provider: once connected, plays its audio_out into `sink`."""

    def __init__(self, inner, sink, linger_s: float = 0.0) -> None:
        self.inner, self.sink, self.linger_s = inner, sink, linger_s
        self._task: asyncio.Task | None = None

    async def connect(self) -> None:
        await self.inner.connect()
        self._task = asyncio.create_task(self.sink.play(self.inner.audio_out()))
        self._task.add_done_callback(_warn_playback)

    async def send_turn(self, audio16k, speaker_label) -> None:
        await self.inner.send_turn(audio16k, speaker_label)

    async def interrupt(self, played_ms: int | None = None) -> None:
        await self.inner.interrupt(played_ms)

    def audio_out(self):
        return self.inner.audio_out()

    async def close(self) -> None:
        if self.linger_s:
            await asyncio.sleep(self.linger_s)
        await self.inner.close()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 10)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()


def _turn_printer():
    n = 0

    def on_record(r) -> None:
        nonlocal n
        n += 1
        decision = "committed" if r.verdict.value == "accept" else "dropped"
        err = f" error={r.error}" if r.error else ""
        print(
            f"turn {n} t={r.t_start:.2f}-{r.t_end:.2f}s speaker={r.speaker or 'unknown'} "
            f"decision={decision} latency={r.latency_s * 1000:.0f}ms{err}",
            flush=True,
        )

    return on_record


async def _demo(args, pipe, sink) -> None:
    if args.input:
        from voiceroom.io import FileSource

        await pipe.run(FileSource(args.input, realtime=args.realtime))
    else:
        from voiceroom.io import MicSource

        print("Listening on the microphone; Ctrl-C to stop.", file=sys.stderr)
        async with MicSource(_device(args.input_device)) as mic:
            await pipe.run(mic)


def cmd_demo(args: argparse.Namespace) -> int:
    if args.debug:  # per dropped turn: the continuity rule + window scores (numbers only)
        import logging

        logging.basicConfig(level=logging.WARNING, format="%(message)s")
        logging.getLogger("voiceroom.pipeline").setLevel(logging.INFO)
    from voiceroom.adapters import MockProvider, OpenAIRealtimeProvider
    from voiceroom.io import SpeakerSink, WavSink
    from voiceroom.pipeline import Pipeline
    from voiceroom.policy import CollaborationPolicy, IsolationPolicy
    from voiceroom.segmenter import Segmenter
    from voiceroom.vad import EnergyVAD

    store = _store(args)
    names = store.list()
    if not names:
        raise CliError("no signatures enrolled; run `voiceroom enroll` first")
    sigs = [store.load(n) for n in names]
    if {s.embedder for s in sigs} != {args.embedder}:
        raise CliError(
            f"stored signatures use embedder(s) {sorted({s.embedder for s in sigs})}, "
            f"but --embedder is {args.embedder!r}; pass --embedder or re-enroll"
        )
    if args.mode == "isolation":
        if not args.owner:
            raise CliError("--owner is required in isolation mode")
        if args.owner not in names:
            raise CliError(f"owner {args.owner!r} is not enrolled (enrolled: {', '.join(names)})")
    try:
        cohort = None if args.no_cohort else load_default(args.embedder)  # AS-norm when shipped
    except (ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise CliError(f"bad cohort file ({exc}); rebuild it or pass --no-cohort") from exc
    if cohort is not None:
        print(
            f"AS-norm cohort active ({len(cohort.embeddings)} windows, threshold "
            f"{cohort.threshold:g} in z-units)",
            file=sys.stderr,
        )
        if args.threshold is not None:
            raise CliError("--threshold is cosine-scale; with a cohort file also pass --no-cohort")
    if args.threshold is not None:
        threshold = args.threshold
    else:
        threshold = cohort.threshold if cohort else threshold_for(args.embedder)
    scale = cohort.score_scale if cohort else 1.0
    if args.mode == "isolation":
        policy = IsolationPolicy(args.owner, threshold, embedder=args.embedder, score_scale=scale)
    else:
        policy = CollaborationPolicy(
            names, args.unknown, threshold, embedder=args.embedder, score_scale=scale
        )

    if args.provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise CliError("OPENAI_API_KEY is not set")
        inner = OpenAIRealtimeProvider()
    else:
        inner = MockProvider()
    linger = OPENAI_LINGER_S if args.provider == "openai" and args.input else 0.0

    matcher = _build_matcher(args.embedder, sigs, threshold, cohort)
    try:
        with contextlib.ExitStack() as stack:
            if args.out:
                sink = stack.enter_context(WavSink(args.out))
            else:
                sink = stack.enter_context(SpeakerSink(_device(args.output_device)))
            pipe = Pipeline(
                EnergyVAD(),
                Segmenter(),
                matcher,
                policy,
                _PlayingProvider(inner, sink, linger),
                matcher_factory=functools.partial(
                    _build_matcher, args.embedder, sigs, threshold, cohort
                ),
                on_record=_turn_printer(),
                # Unpaced replay: turns outrun playback, so barge-in would chop every reply.
                sink=None if args.input and not args.realtime else sink,
                # Unpaced file replay feeds turns far faster than real time: don't overflow.
                **({"max_pending": 1000} if args.input and not args.realtime else {}),
            )
            asyncio.run(_demo(args, pipe, sink))
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    except RuntimeError as e:  # e.g. sounddevice/PortAudio missing
        raise CliError(str(e)) from e
    return 0


# ---- entry point ------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--store", help=f"signature directory (env {STORE_ENV}; default {DEFAULT_STORE})"
    )
    common.add_argument("--keyfile", type=Path, help="encryption keyfile (env VOICEROOM_KEY wins)")

    p = argparse.ArgumentParser(prog="voiceroom", description="Speaker-aware realtime voice AI")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("enroll", parents=[common], help="record or load a voice signature")
    e.add_argument("--name", required=True)
    e.add_argument("--file", help="enroll from this audio file instead of the microphone")
    e.add_argument("--yes", action="store_true", help="give consent non-interactively")
    e.add_argument("--force", action="store_true", help="replace an existing signature")
    e.add_argument("--embedder", default=DEFAULT_EMBEDDER)
    e.add_argument("--seconds", type=float, default=ENROLL_SECONDS, help="mic recording length")
    e.add_argument("--input-device", help="mic device index or name")
    e.set_defaults(fn=cmd_enroll)

    sub.add_parser("list", parents=[common], help="list enrolled signatures").set_defaults(
        fn=cmd_list
    )

    d = sub.add_parser("delete", parents=[common], help="delete a signature")
    d.add_argument("--name", required=True)
    d.set_defaults(fn=cmd_delete)

    m = sub.add_parser("demo", parents=[common], help="run the live pipeline")
    m.add_argument("--mode", choices=["isolation", "collaboration"], default="isolation")
    m.add_argument("--owner", help="enrolled name whose turns are forwarded (isolation)")
    m.add_argument("--provider", choices=["openai", "mock"], default="openai")
    m.add_argument("--input", help="audio file to use instead of the microphone")
    m.add_argument("--input-device", help="mic device index or name")
    m.add_argument("--output-device", help="speaker device index or name")
    m.add_argument("--out", help="write the model's reply audio to this wav instead of playing")
    m.add_argument("--embedder", default=DEFAULT_EMBEDDER)
    m.add_argument(
        "--threshold",
        type=float,
        help="cosine match threshold (default: per embedder; needs --no-cohort if a cohort exists)",
    )
    m.add_argument(
        "--no-cohort",
        action="store_true",
        help="ignore data/cohort-<embedder>.npz (cosine scoring)",
    )
    m.add_argument("--unknown", choices=["drop", "label"], default="drop", help="collaboration")
    m.add_argument("--realtime", action="store_true", help="pace --input at wall-clock speed")
    m.add_argument(
        "--debug", action="store_true", help="log which continuity rule dropped a turn + scores"
    )
    m.set_defaults(fn=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (CliError, StoreError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
