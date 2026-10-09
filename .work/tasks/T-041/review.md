# T-041 review: cross-talk bleed suppression

`make check`: green (29 scripts, 25.5 s; smoke_room_bleed.py 2.5 s).
I checked the findings below with a throwaway probe (`/tmp/t041rev/probe.py`, not committed). It reuses the smoke harness with realtime frame pacing.

## Findings

[HIGH] src/voiceroom/room.py:349 (`_bleed_of`, pending-turn lookup) together with the commit order in `_committer`: the bleed copy is only caught if the louder original is already in `_seen` or `_entries`. Commits go in `t_end` order, and `_others_caught_up` only waits until the other source has streamed past `copy.t_end + end_silence`. If the quieter copy's VAD end comes even slightly earlier than the original's, the copy commits first. The original's segmenter has not emitted its turn yet at that point (that happens at `orig.t_end + end_silence`), so `pend` and `_seen` are both empty and the copy is committed. Later the louder original is committed too, which gives a duplicate. This is normal on real mics, because the attenuated, noisy tail of a −12 dB copy drops below VAD threshold one or two frames sooner.
  → Reproduced with realtime pacing (`sleep(FRAME_SIZE/SR)` per frame), alice on track a and a −12 dB/15 ms copy on b whose tail is 60 ms shorter: `[('b','ACCEPT',None,2.94,None), ('a','ACCEPT','synth000',3.01,None)]`, 2 provider turns. The aligned copy is caught. The smoke misses this because `play()` feeds frames with `sleep(0)`, so every source is far ahead of the committer and every turn is already enqueued.
  → Fix: when a non-verified accepted turn has a live overlapping source that has not yet emitted a turn covering its span, hold it, bounded by the catch-up deadline plus the other source's `end_silence`. One way is to wait until each overlapping source has streamed past `e.t_start + (e.t_end - e.t_start) * (1 - BLEED_MIN_OVERLAP) + end_silence`, or until that source has an open/ongoing segment that ends. Alternatively, retract a committed copy, which isn't possible once sent, so holding is the right choice. Also make the smoke pace at least one scenario in realtime (or simulated time) and include a copy that ends earlier.

[MEDIUM] src/voiceroom/room.py:334 and :362: the identity a committed turn is remembered under is `e.claimed or dt.label`, but the turn was sent under `dt.label`. On a verified track with `on_mismatch="relabel"`, an impostor (carol on alice's device) is committed as carol but remembered as alice. Bob's mic picks up a copy that matches carol, so `ident in (dt.match.name, dt.label)` is `'alice' in ('carol','carol')`, which is false, and the copy is not dropped.
  → Reproduced: carol on a, a −10 dB/5 ms noiseless copy on b gives `[('a','ACCEPT','synth002'), ('b','ACCEPT','synth002')]`, so carol is committed twice.
  → Fix: remember `dt.label`, the identity actually committed. Optionally also accept a match on `e.claimed`.

[MEDIUM] src/voiceroom/room.py:364-368: the level gate compares absolute speech RMS across different devices. Mic gain and distance differences between devices (laptop mic vs headset) routinely exceed 10 dB, so the 3 dB margin is not a reliable bleed signal. Two failure scenarios:
  (a) Genuine speech gets dropped. A turn that does not verify as its track owner is dropped whenever a louder turn on another track overlaps it ≥ 50 % and `dt.match.name is None`. That covers a short owner interjection ("yes", < 1 s) that fails verification and is accepted as Unknown under `unknown="label"`, any speaker on a shared track (`claimed is None`, so the protection on line 345 never applies), and an unenrolled guest in collaboration mode. Real speech said over a louder talker is silently lost, and the record shows `bleed_of`.
  (b) Duplicates when the original's mic has lower gain. Alice is on a low-gain mic and bob's hot mic hears her copy at +3 dB relative to her own track. The copy is "louder", so it is never dropped, and alice is committed twice (the copy matches alice and is relabelled).
  → Fix: compare the same signal on both tracks instead of absolute RMS. For example, use the energy ratio over the overlapping window, or the cross-correlation peak within ±50 ms of the two tracks' audio over the overlap. Real bleed is highly correlated at a small lag. Genuine simultaneous speech is not. Require correlation plus a level drop before dropping a turn whose label is Unknown.

[MEDIUM] scripts/smoke/smoke_room_bleed.py:118: the "true simultaneous speech yields two commits" check passes without exercising the detector. Both turns verify as their track owners, so `_bleed_of` returns at line 345 before the overlap or level logic runs. The levels are also roughly equal, so the 3 dB gate would not fire anyway. Nothing checks that an unverified-but-genuine turn under louder cross-talk survives (see the previous finding).
  → Failure scenario: a regression that drops genuine overlapped Unknown/shared-track speech still passes `make check`.
  → Fix: add a case with a shared track (`claimed=None`) or a short unverified owner turn that overlaps a louder speaker on another track, and assert it is committed.

[LOW] scripts/smoke/smoke_room_bleed.py:126: the acceptance criterion says that with bleed off, results should match smoke_room_mux. The smoke compares detector on vs off on its own two-speaker mux built inside the script, not the smoke_room_mux scenario (3 sources, shared track, impostor).
  → Failure scenario: the detector changes results on the shared-track or impostor layout from smoke_room_mux (exactly where findings 2 and 3a bite), and nothing catches it.
  → Fix: run the smoke_room_mux conversation with `bleed=True` and `bleed=False` and compare the records.

[LOW] src/voiceroom/synth.py:124-127: `add_bleed` does not validate `delay_ms`. A negative delay gives a negative `d`, so `copy[d : d + m]` silently writes a wrong or empty slice.
  → Failure scenario: a bench sweep that includes 0 or negative jitter produces a track with no bleed and reports a falsely perfect duplicate rate.
  → Fix: `if delay_ms < 0: raise ValueError`.

[LOW] .work/tasks/T-041/: there is no `plan.md`, while `agentops/config.json` has `require_plan_approval: true`.
  → Failure scenario: the change lands without an approved plan, and the design choice (absolute-RMS gate, no correlation) was never reviewed.
  → Fix: add the plan, and record the bleed-detection approach in decisions.md.

## Checked and OK
- Privacy: `_Seen` stores only span, level and identity. There is no audio or embedding. Records add only `bleed_of` (a source id). Log lines are unchanged.
- `asyncio.wait([])` cannot be reached: `e.task` is assigned right after `_Entry` is created and before it is appended.
- `_seen` is bounded (`BLEED_HISTORY=64`). `_level_db` is O(turn) numpy and runs once per commit, which is fine.
- No provider-specific code outside adapters.
