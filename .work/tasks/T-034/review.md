# Review T-034: portable (plain npz) AS-norm cohort

`make check`: green (26 scripts, 0 failed, 37.9 s). The on-disk `data/cohort-wespeaker-resnet34.npz` has already been rebuilt as a plain zip (`PK` header). No `plan.md` in the task dir.

## Findings

[MEDIUM] scripts/live/run_macos_demo.sh:128,147: the guided demo script still passes `--no-cohort` to both `demo` runs. The diff only removed the flag from `docs/demo-macos.md`. → The doc calls this script "a guided version of this checklist", and the human used it for T-023. A rerun (and T-035's real-mic FR analysis, which depends on T-034) would silently use cosine scoring, so the acceptance item "run the demo without `--no-cohort`" is not met on the path the human actually uses. → Drop `--no-cohort` from both lines, or make it an opt-in flag.

[MEDIUM] docs/demo-macos.md:16-17: the doc says "copy it with the repo data", but `data/` is gitignored (`.gitignore`: `data/`), so the cohort file does not come with a clone/pull. The doc gives no command for getting it onto the Mac. → On a fresh machine `load_default` returns None and the demo quietly runs in cosine mode. The only signal is that the "AS-norm cohort active" line is missing, so the tester thinks the cohort path was tested when it was not. → State how to get the file (copy `data/cohort-wespeaker-resnet34.npz` explicitly, or `uv run python -m voiceroom.bench.cohort` with LibriSpeech), and have `demo` print a one-line "no cohort file at <path>; cosine scoring" notice when it is absent.

[MEDIUM] src/voiceroom/cohort.py:64-77 (load) / :33-40 (`__post_init__`): removing Fernet also removed integrity protection. The file carries its own accept `threshold`, which `Matcher` adopts in place of config, and only finiteness is checked. → The doc now tells users to copy this file between machines. A stale, corrupted or tampered file (e.g. `threshold=-50` or a huge `score_scale`) passes validation, and every speaker gets forwarded as the owner in isolation mode. The only clue is a stderr number. → Range-check on load (e.g. `0 < threshold < 10` z-units, `1 <= score_scale <= 50`, unit-norm rows, `top_n <= len(embeddings)`), or record the expected SHA-256 next to the embedder in code/reports and verify it.

[LOW] .work/decisions.md:76 (D-013): it still describes the cohort as "Fernet-encrypted npz, mode 0600, signature-store key". Nothing records the switch to plaintext or why this is OK under the CLAUDE.md "encrypt stored signatures" rule (it holds public LibriSpeech embeddings and speaker ids, not user signatures). → A later agent following D-013 could re-add encryption and bring back the T-023 failure. → Add a short D-0xx entry (what/why/alternative: per-machine build).

[LOW] scripts/smoke/smoke_e2e_cohort.py:1: the docstring still says "encrypted cohort file". → This misleads anyone maintaining the smoke. → Change it to "plain cohort file".

[LOW] src/voiceroom/cohort.py:43: the `save` docstring says "no audio or speaker data", but the file stores `speakers` (corpus speaker ids). → A future privacy review could rely on this claim. → Reword to "public-corpus speaker ids only".

Counts: HIGH 0, MEDIUM 3, LOW 3.
