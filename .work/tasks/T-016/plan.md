# T-016 plan
1. `policy.py`: `IsolationPolicy(owner)`, `decide(match, final)` -> Decision(ACCEPT|REJECT|UNDECIDED, label); early decisions need a score/margin buffer beyond the matcher threshold.
2. `pipeline.py`: async `Pipeline.run(frame_source)`: VAD -> Segmenter; IdentifyReady starts matcher in a thread; TurnEnd awaits it, decides early if confident, else re-identifies on full turn audio; accepted -> `provider.send_turn`, rejected audio dropped. Records TurnRecord (decision, early, endpoint + compute latency).
3. `scripts/smoke/smoke_e2e_isolation.py`: 3 synthetic convs (toy embedder, EnergyVAD, MockProvider); assert 0 non-owner commits, >=90 % owner turns, print p50/p95.
4. make check; reviewer; summary.
