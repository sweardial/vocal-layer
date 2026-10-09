# T-021 plan
1. `src/voiceroom/cli.py` (argparse): enroll (consent prompt/--yes, mic ~25 s or --file), list, delete, demo; `[project.scripts] voiceroom`.
2. Common opts: `--store` (env VOICEROOM_STORE, default ~/.voiceroom/signatures), `--keyfile`; key via store.load_key.
3. demo: all stored signatures -> Matcher (factory for process isolation), Isolation/CollaborationPolicy, provider mock|openai, FileSource|MicSource, WavSink|SpeakerSink via a provider wrapper that plays audio_out; one line per turn via on_record.
4. `scripts/smoke/smoke_cli.py`: subprocess enroll x2 -> list -> demo (toy, mock) -> delete -> list empty.
5. `make check`, reviewer, summary.
