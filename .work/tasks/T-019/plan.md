# T-019 plan: OpenAI Realtime GA adapter + fake server

## Approach
- Use raw `websockets` (already pinned `>=13,<16`) via `websockets.asyncio.client.connect`, not the `openai` SDK. That means one less dependency and no SDK churn, the fake server speaks the same JSON, and provider types stay inside `adapters/`.
- We own turn-taking (D-007), so `turn_detection: null`, and each `send_turn` is one item sequence. A single reader task parses server events into an `asyncio.Queue[AudioOut|None]`.
- Resample 16k to 24k with `voiceroom.audio.resample`. Output stays at 24 kHz (`AudioOut.sample_rate=24000`), and the sink resamples if it needs to. This avoids a lossy round trip.
- Docs (GA, checked 2026-10-08). C = https://developers.openai.com/api/reference/resources/realtime/client-events, S = .../realtime/server-events, G = https://developers.openai.com/api/docs/guides/realtime-conversations, W = https://developers.openai.com/api/docs/guides/realtime-websocket

## Wire protocol (exact shapes)
- Connect (W): `wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1`, header `Authorization: Bearer $OPENAI_API_KEY`. No `OpenAI-Beta` header. Wait for `session.created` (S), then send (C):
  `{"type":"session.update","session":{"type":"realtime","output_modalities":["audio"],"instructions":<opt>,
   "audio":{"input":{"format":{"type":"audio/pcm","rate":24000},"turn_detection":null},
            "output":{"format":{"type":"audio/pcm","rate":24000},"voice":<cfg, default "marin">}}}}`
  Then wait for `session.updated` (S), with a timeout.
- Attribution, only when `speaker_label is not None` (C, settles the D-007 uncertainty):
  `{"type":"conversation.item.create","item":{"type":"message","role":"user","content":[{"type":"input_text","text":"[Speaker: alice]"}]}}`
- Audio (C, G): `{"type":"input_audio_buffer.append","audio":<b64 pcm16 LE mono 24k>}`. The limit is 15 MiB per event. We send 100 ms chunks (4800 samples, 9600 B, always an even byte count). Then `{"type":"input_audio_buffer.commit"}` (an empty buffer is an error) and `{"type":"response.create"}`.
- Server events handled (S): `response.created` (`response.id`, which starts a new reply_id), `response.output_item.added` (`item.id`, `response_id`), `response.output_audio.delta` (`response_id, item_id, output_index, content_index, delta`), `response.output_audio.done`, `response.done` (`response.status` in completed|cancelled|failed|incomplete), `error` (`error.{type,code,message,param,event_id}`, logged without payload audio, session stays open). Everything else is ignored.
- Barge-in (C): `{"type":"response.cancel"}`, sent only while a response is in progress, because if nothing is running the server returns an error. Plus `{"type":"conversation.item.truncate","item_id":<last assistant audio item>,"content_index":0,"audio_end_ms":min(played_ms, received_ms)}`, sent only if `played_ms is not None` and an item exists. The server errors if the value exceeds the audio duration, which is why we clamp to the received ms.

## Files
- `src/voiceroom/adapters/openai_realtime.py`:
  `class OpenAIRealtimeProvider:` `__init__(self, model: str = DEFAULT_MODEL, voice: str = "marin", instructions: str | None = None, url: str | None = None, api_key_env: str = "OPENAI_API_KEY", connect_timeout_s: float = 10.0)`.
  The key is read from env in `connect()` (raises `RuntimeError` if missing) and never logged or put in `repr`. `url` overrides the base URL for the fake server, and in that case the key is optional.
  It implements `Provider`: `connect`, `send_turn(audio16k, speaker_label)`, `interrupt(played_ms=None)`, `audio_out()` (single consumer, ends on close), `close`.
  `send_turn` validates the input exactly like MockProvider before sending anything. It serializes sends with an `asyncio.Lock`, so one turn's events are contiguous.
  Helpers: `_encode_pcm16(x: np.ndarray) -> str` (clip, `*32767`, `<i2`, b64) and `_decode_pcm16(b64: str) -> np.ndarray` (float32).
  Interrupt state: `_active_resp_id`, `_last_item_id`, `_received_samples[item]`, `_cancelled: set[str]`. Deltas for cancelled response ids are dropped. `response.done` emits `AudioOut(np.zeros(0, float32), 24000, final=True, reply_id)` unless the response was cancelled.
- `src/voiceroom/adapters/__init__.py`: export `OpenAIRealtimeProvider` (the module imports only `websockets` and numpy).
- `scripts/smoke/fake_realtime_server.py`: `async def serve_fake(host="127.0.0.1", port=0) -> FakeServer`, with `.url`, `.events: list[dict]` (with `audio`/`delta` fields replaced by their byte length; no audio is stored), and `.close()`.
  It sends `session.created`, answers `session.update` with `session.updated`, and answers `response.create` with `response.created` → `output_item.added` → N `output_audio.delta` (24 kHz tone, sent with small sleeps) → `output_audio.done` → `response.done`. On `response.cancel` it stops streaming and sends `response.done` with status `cancelled`. It also has a `--port` CLI.
- `scripts/smoke/smoke_openai_adapter.py`: runs fully on localhost.
- `scripts/live/openai_realtime_check.py`: `--wav`, `--label`, `--model`. Sends one turn and writes the reply to `data/out/openai_reply.wav`. It prints only event types and timings. It is not run by `make check`.

## Test plan (smoke_openai_adapter.py, < 5 s)
1. The first client event is `session.update`, and `session.audio.input.turn_detection is None` and the format is `audio/pcm` at 24000.
2. Run a scripted list of turns `[(owner, accepted), (guest, dropped), (owner, accepted)]` through a tiny gate loop that calls `send_turn` only for accepted turns. Assert that the server event types per turn are exactly `append+ → commit → response.create`, that there are exactly 2 commits, and that the summed append bytes ≈ `len(audio)*1.5*2`.
3. With `speaker_label="alice"`, `conversation.item.create` comes immediately before the first append, with the exact content shape above. With `None`, there is no item.create.
4. The decoded `audio_out` chunks have `sample_rate == 24000` and a total sample count equal to the server's tone length, and the last chunk has `final=True`.
5. An interrupt mid-reply (`played_ms=50`) sends `response.cancel` and a truncate with `content_index` 0 and `audio_end_ms` 50, and no further chunks arrive for that reply_id. An interrupt when idle sends no `response.cancel`.
6. Calling `send_turn` with empty audio raises `ValueError`, and the server receives 0 new events.
7. The API key string ("sk-test-SECRET") never appears in captured logs or in `repr(provider)`.

## Risks / unknowns
- GA docs now also describe "GPT-Live" (`/v1/live/sessions`). D-007 stays on Realtime (`gpt-realtime-2.1`, the model in all Realtime examples). The implementer should not switch.
- Two consecutive user items (text + audio) is the documented-legal pattern. If live testing shows weak attribution, a fallback is one item with `input_text` + `input_audio` content. That is a follow-up task, not this one.
- websockets 13–15: use `websockets.asyncio.client.connect(url, additional_headers=...)` and `websockets.asyncio.server.serve`. Verify locally against the installed version (`uv pip show websockets`).
- A truncate/cancel race: the server may finish before our cancel arrives, which returns an `error` event. Log its code, keep the session open, and don't raise.
