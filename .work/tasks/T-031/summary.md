# T-031 summary

No code change. The acceptance bar is already met by the shipped D-015 rule (0.5 s windows, 3 bad windows in a row, foreign-enrolled guard, drop 0.4, buffer 0.1): owner FR 10.0 % (6/60), leak of 1 s interjections 3.3 %, 2 s 0 % (`reports/windows-wespeaker-resnet34-run3.md`). Pipeline bench: FR 4.3 %.

I re-ran the 1 s windows (`--win-frames 32`, buffers 0.1/0.2) with bad-run 1 and bad-run 2:
- run 1, buffer 0.1, drop 0.4: FR 11.7 %, leak 1 s 1.7 %, 0.5 s 48 % (0.5 s windows with run 3: FR 10 %, 0.5 s leak 42 %).
- run 1, buffer 0.2, drop 0.4: FR 8.3 %, leak 1 s 6.7 % (over 5 %).
- run 2 gets lower FR only by leaking much more (drop 0.3: FR 6.7 %, leak 1 s 20 %).

So 1 s windows are no better than the shipped rule on either axis, and they add ~0.5 s of window latency. Left WIN_FRAMES = 16 and the config as is. The sub-1 s interior leak (~42 %) is bounded by window size and was not fixed.

Verification: `make check` green (25 scripts, 0 failed). New reports: `reports/windows-wespeaker-resnet34-w32.md` (regenerated), `reports/windows-wespeaker-resnet34-w32-run2.md`.
Follow-up: none needed. VAD-aware splitting could cut the sub-1 s leak, but T-029's needs_human is still unanswered.
