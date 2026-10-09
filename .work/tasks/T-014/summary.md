# T-014 summary
- Added `src/voiceroom/segmenter.py`: streaming `Segmenter.process(frame, prob)` / `flush()` emitting `SpeechStart`, `IdentifyReady(audio, t_start)` (after `identify_after_s` of *voiced* frames, default MIN_DECISION_S), `TurnEnd(audio, t_start, t_end, split)` (400 ms hangover, 200 ms pre-roll, 100 ms tail, `max_turn_s` split, inf allowed).
- Added `scripts/smoke/smoke_segmenter.py` (EnergyVAD + synthetic conversation; 10/10 turns within ±300 ms, no turn spans two speakers, split/edge cases).
- Reviewer MEDIUMs fixed: split no longer yields silent turns/crashes (resumes on next speech frame); identify counts speech frames only. Review: .work/tasks/T-014/review.md.
- Verified: `make check` green.
- Follow-ups: none.
