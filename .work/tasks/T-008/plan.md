1. eval/conversations.py: split_utterances (seeded, disjoint enroll/test), make_conversation, save/load (wav+json).
2. Turns: random speaker != previous, random test utterance, pause uniform in range between turns; segments in sample-rounded seconds.
3. Noise: white gaussian at SNR relative to speech power; seeded rng.
4. Smoke on SynthCorpus checks overlap/sort, audio match, pauses, determinism, split disjoint, save/load.
5. make check.
