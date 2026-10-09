# T-029: interior-interjection leak vs owner false-rejection

With 0.5 s windows there is no setting that gives owner FR < 5 % and interior-interjection leak < 2 % (windows bench, wespeaker, 60 owner turns / 180 interjection turns):

| rule | owner FR | leak 0.5 s | leak 1 s | leak 2 s |
|---|---|---|---|---|
| old (any bad window, drop 0.3) | 65 % | 3 % | 0 % | 0 % |
| shipped: 3 bad in a row + foreign-enrolled guard, drop 0.4 | 10 % | 42 % | 3.3 % | 0 % |
| 4 in a row, no guard | 5-8 % | 83 % | 15-27 % | 2 % |

Pipeline bench (whole segments, no interior interjections): FR 4.3 %, FA 1.6 %.

Question: which default for isolation mode?
1. (current) Shipped rule: usable FR, interjections >= 1 s mostly blocked, < 1 s ones leak. 
2. Old strict rule: SPEC FA safe, but owner FR ~65 % (isolation unusable).
3. Keep 1, and build 1 s windows / VAD-aware splitting in T-031 to cut the 0.5 s leak.
Recommendation: 1 + 3.
