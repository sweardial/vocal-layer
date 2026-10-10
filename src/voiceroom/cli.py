"""`voiceroom` command line: enroll, list, delete, demo, session, token, room (no audio out)."""

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


def _load_sigs(store: SignatureStore, embedder: str) -> list[Signature]:
    names = store.list()
    if not names:
        raise CliError("no signatures enrolled; run `voiceroom enroll` first")
    sigs = [store.load(n) for n in names]
    if {s.embedder for s in sigs} != {embedder}:
        raise CliError(
            f"stored signatures use embedder(s) {sorted({s.embedder for s in sigs})}, "
            f"but --embedder is {embedder!r}; pass --embedder or re-enroll"
        )
    return sigs


def _scoring(args: argparse.Namespace) -> tuple[Cohort | None, float, float]:
    """(cohort, threshold, score scale) from --embedder/--threshold/--no-cohort."""
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
    return cohort, threshold, cohort.score_scale if cohort else 1.0


def _debug_logging() -> None:
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logging.getLogger("voiceroom.pipeline").setLevel(logging.INFO)


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
        _debug_logging()
    from voiceroom.adapters import MockProvider, OpenAIRealtimeProvider
    from voiceroom.io import SpeakerSink, WavSink
    from voiceroom.pipeline import Pipeline
    from voiceroom.policy import CollaborationPolicy, IsolationPolicy
    from voiceroom.segmenter import Segmenter
    from voiceroom.vad import EnergyVAD

    store = _store(args)
    names = store.list()
    sigs = _load_sigs(store, args.embedder)
    if args.mode == "isolation":
        if not args.owner:
            raise CliError("--owner is required in isolation mode")
        if args.owner not in names:
            raise CliError(f"owner {args.owner!r} is not enrolled (enrolled: {', '.join(names)})")
    cohort, threshold, scale = _scoring(args)
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


# ---- sessions / tokens / room -------------------------------------------------------------

SESSIONS_ENV = "VOICEROOM_SESSIONS"
BOT_IDENTITY = "voiceroom-bot"


def _sessions(args: argparse.Namespace):
    from voiceroom.sessions import SessionStore

    sigs = _store(args)
    root = args.sessions or os.environ.get(SESSIONS_ENV) or sigs.root / "sessions"
    return SessionStore(root, sigs._key, sigs)


def _session(store, sid: str):
    try:
        return store.get(sid)
    except KeyError:
        raise CliError(f"no session {sid!r}") from None


def cmd_session(args: argparse.Namespace) -> int:
    from voiceroom.sessions import SessionError

    store = _sessions(args)
    try:
        if args.action == "create":
            s = store.create_session(args.owner, args.mode, args.unknown, args.room)
            print(f"{s.id}\t{s.room}")
        elif args.action == "invite":
            ttl = {"ttl_s": args.ttl_hours * 3600.0} if args.ttl_hours is not None else {}
            print(store.invite(_session(store, args.session), args.user, **ttl))
        elif args.action == "accept":
            s = store.accept(args.code)
            print(f"joined session {s.id} ({len(s.members)} members)")
        elif args.action == "revoke":
            store.revoke(_session(store, args.session), args.user)
            print(f"revoked {args.user}")
        else:
            for s in store.list():
                print(f"{s.id}\t{s.mode}\t{s.room}\towner={s.owner}\tmembers={','.join(s.members)}")
    except SessionError as e:  # includes InviteError
        raise CliError(str(e)) from e
    return 0


def _livekit_url(args: argparse.Namespace) -> str:
    from voiceroom.adapters.livekit_room import DEV_URL

    return args.url or os.environ.get("LIVEKIT_URL") or DEV_URL


def _need_livekit():
    try:
        from voiceroom.adapters import livekit_room
    except ImportError as e:
        raise CliError(str(e)) from e
    return livekit_room


def cmd_token(args: argparse.Namespace) -> int:
    from voiceroom.sessions import SessionError

    store = _sessions(args)
    _need_livekit()
    try:
        print(store.join_token(_session(store, args.session), args.user, _livekit_url(args)))
    except (SessionError, ValueError) as e:
        raise CliError(str(e)) from e
    return 0


def _fake_room(path: str, realtime: bool):
    import json

    from voiceroom.rooms import FakeParticipant, FakeRoom

    try:
        spec = json.loads(Path(path).read_text())
        base = Path(path).parent
        parts = [
            FakeParticipant.from_wav(
                p["id"],
                str(base / p["wav"]),
                identity=p.get("identity"),
                attributes=p.get("attributes", {}),
                join_at=p.get("join_at", 0.0),
                leave_at=p.get("leave_at"),
            )  # fmt: skip
            for p in spec["participants"]
        ]
    except (ValueError, KeyError, TypeError, RuntimeError) as e:
        raise CliError(f"bad --script {path}: {type(e).__name__}") from e
    return FakeRoom(parts, realtime=realtime)


def _room_printer():
    n = 0

    def on_record(r) -> None:
        nonlocal n
        n += 1
        decision = "committed" if r.verdict.value == "accept" else "dropped"
        err = f" error={r.error}" if r.error else ""
        bleed = f" bleed_of={r.bleed_of}" if r.bleed_of else ""
        print(
            f"turn {n} t={r.t_start:.2f}-{r.t_end:.2f}s source={r.source_id} "
            f"claimed={r.claimed_identity or '-'} decision={decision} "
            f"label={r.speaker or UNKNOWN_LABEL} latency={r.latency_s * 1000:.0f}ms{err}{bleed}",
            flush=True,
        )

    return on_record


async def _close_after(seconds: float, transport) -> None:
    await asyncio.sleep(seconds)
    await transport.close()  # ends the event stream; the agent drains and stops


async def _run_room(args, session, store, sigs, scoring, provider) -> int:
    """Everything that touches the transport runs inside the loop (LiveKit binds to it)."""
    from voiceroom.room import RoomPipeline
    from voiceroom.rooms import RoomAgent
    from voiceroom.sessions import SessionSync
    from voiceroom.vad import EnergyVAD, SileroVAD

    cohort, threshold, scale = scoring
    policy_kw = {"threshold": threshold, "embedder": args.embedder, "score_scale": scale}
    if args.verify_threshold is not None:
        policy_kw["verify_threshold"] = args.verify_threshold
    if args.window_drop is not None:
        policy_kw["window_drop"] = args.window_drop

    if args.transport == "fake":
        transport = _fake_room(args.script, args.realtime)
        kick = transport.kick
    else:
        lk, url = _need_livekit(), _livekit_url(args)
        try:
            transport = lk.LiveKitRoom(url, lk.make_token(url, session.room, BOT_IDENTITY))
            kick = lk.make_kicker(url, session.room)
        except ValueError as e:
            raise CliError(str(e)) from e

    unpaced = args.transport == "fake" and not args.realtime
    holder: dict = {}
    pipeline = RoomPipeline(
        provider,
        lambda sid, claimed: holder["sync"].policy_factory(**policy_kw)(sid, claimed),
        _build_matcher(args.embedder, sigs, threshold, cohort),
        vad_factory=EnergyVAD if args.vad == "energy" else SileroVAD,
        matcher_factory=functools.partial(_build_matcher, args.embedder, sigs, threshold, cohort),
        on_record=_room_printer(),
        # unpaced scripted replay feeds audio far faster than real time: don't overflow
        **({"max_pending": 1000, "max_inflight_identify": 1000} if unpaced else {}),
    )
    agent = RoomAgent(transport, pipeline, provider)
    holder["sync"] = sync = SessionSync(store, session.id, pipeline, kick, agent=agent)
    tasks = [asyncio.create_task(sync.run())]
    if args.duration is not None:
        tasks.append(asyncio.create_task(_close_after(args.duration, transport)))
    try:
        await agent.run()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await sync.sync_once()  # a revocation that landed just before the end
    return len(sync.kicked)


def cmd_room(args: argparse.Namespace) -> int:
    if args.debug:
        _debug_logging()
    from voiceroom.adapters import MockProvider, OpenAIRealtimeProvider

    store = _sessions(args)
    session = _session(store, args.session)
    sigs = _load_sigs(store.signatures, args.embedder)
    scoring = _scoring(args)
    if args.transport == "fake" and not args.script:
        raise CliError("--transport fake needs --script (a JSON file of scripted participants)")
    if args.provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise CliError("OPENAI_API_KEY is not set")
        provider = OpenAIRealtimeProvider()
    else:
        provider = MockProvider()
    try:
        kicked = asyncio.run(_run_room(args, session, store, sigs, scoring, provider))
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 0
    except RuntimeError as e:
        raise CliError(str(e)) from e
    if kicked:
        print(f"removed {kicked} revoked participant(s)", file=sys.stderr)
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

    common.add_argument(
        "--sessions", help=f"session directory (env {SESSIONS_ENV}; default <store>/sessions)"
    )
    se = sub.add_parser("session", help="manage sessions and invites")
    ss = se.add_subparsers(dest="action", required=True)
    c = ss.add_parser("create", parents=[common], help="create a session; prints id and room")
    c.add_argument("--owner", required=True, help="enrolled name")
    c.add_argument("--mode", choices=["isolation", "collaboration"], default="isolation")
    c.add_argument("--unknown", choices=["drop", "label"], default="drop")
    c.add_argument("--room", help="room name (default vr-<id>)")
    i = ss.add_parser("invite", parents=[common], help="print a single-use invite code")
    i.add_argument("--session", required=True)
    i.add_argument("--user", required=True, help="enrolled name")
    i.add_argument("--ttl-hours", type=float, help="invite lifetime (default 24)")
    a = ss.add_parser("accept", parents=[common], help="consume an invite code")
    a.add_argument("--code", required=True)
    r = ss.add_parser("revoke", parents=[common], help="remove a member")
    r.add_argument("--session", required=True)
    r.add_argument("--user", required=True)
    ss.add_parser("list", parents=[common], help="list sessions")
    se.set_defaults(fn=cmd_session)

    t = sub.add_parser("token", parents=[common], help="print a LiveKit join token for a member")
    t.add_argument("--session", required=True)
    t.add_argument("--user", required=True)
    t.add_argument("--url", help="LiveKit URL (env LIVEKIT_URL; default local dev server)")
    t.set_defaults(fn=cmd_token)

    ro = sub.add_parser("room", parents=[common], help="run the room agent for a session")
    ro.add_argument("--session", required=True)
    ro.add_argument("--transport", choices=["livekit", "fake"], default="livekit")
    ro.add_argument("--provider", choices=["openai", "mock"], default="openai")
    ro.add_argument("--embedder", default=DEFAULT_EMBEDDER)
    ro.add_argument("--threshold", type=float, help="cosine match threshold (needs --no-cohort)")
    ro.add_argument("--no-cohort", action="store_true")
    ro.add_argument("--verify-threshold", type=float, help="1:1 track verification threshold")
    ro.add_argument("--window-drop", type=float, help="continuity window drop (tuning)")
    ro.add_argument("--vad", choices=["silero", "energy"], default="silero")
    ro.add_argument("--url", help="LiveKit URL (env LIVEKIT_URL; default local dev server)")
    ro.add_argument("--script", help="fake transport: JSON file listing scripted participants")
    ro.add_argument("--realtime", action="store_true", help="fake transport: wall-clock pacing")
    ro.add_argument("--duration", type=float, help="leave the room after this many seconds")
    ro.add_argument("--debug", action="store_true", help="log which continuity rule dropped a turn")
    ro.set_defaults(fn=cmd_room)
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
