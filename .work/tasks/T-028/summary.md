# T-028 summary
- Added `python -m voiceroom.bench.windows` (owner turns vs spliced interior interjections, sweep window_drop x window_buffer, optional `--win-frames 32`); reports `reports/windows-*.md`.
- `WINDOW_DROPS` wespeaker/ecapa 0.05 -> 0.3 (0.05 rejected 100 % of owner turns). Recorded in D-014.
- Finding: with 0.5 s windows owner FR stays >= ~63 % at any drop (best==owner test dominates), leak ~1 %. 1 s windows: FR 22-28 %. Not viable yet -> follow-up T-031.
- Verified: `make check` green (25 smokes, incl. new smoke_windows_bench.py); bench run on dev-clean, 30 trials, wespeaker + ecapa.
- Review fixes: D-014 corrected (buffer matters); bench lacks VAD/AS-norm. Backlog has two T-028 entries (line 148 is a different task; not touched). smoke_io flaked once under load (passes alone); make check green on rerun.
