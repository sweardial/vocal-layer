# T-005 summary
Added `src/voiceroom/store.py` (`SignatureStore`, `load_key`, `StoreError`/`StoreKeyError`) and `scripts/smoke/smoke_store.py`; added `cryptography` dep.
- One Fernet-encrypted JSON file per signature; filenames are HMAC(key, name) so they don't reveal names. Consent metadata + timestamp stored; `metadata(name)` reads them.
- Key: `VOICEROOM_KEY` else keyfile (0600, O_EXCL). Wrong/malformed key -> `StoreKeyError`.
- Review fixes (MEDIUM): load verifies stored name matches (swapped-file defence); keyed filenames; `list()` skips undecryptable files unless none decrypt.
## Verification
`make check`: lint clean, 5 smoke scripts pass (~5 s).
## Follow-ups / known
- Key rotation changes filenames (needs re-save migration when MultiFernet is added).
- Not done (LOW): fsync before rename, record format/embedder version field, empty-keyfile recovery, keyfile perms check.

## Fix round 2
- HIGH: existing keyfiles are now rejected unless a regular file, owned by the current user, with no group/other bits (clear `chmod 600` message); key dir created 0700.
- MEDIUM: `save` uses a unique `mkstemp` file in the store dir, fsyncs, atomically replaces, and cleans up on failure (also covers the earlier fsync LOW).
- MEDIUM: keyfile is written fully to a private temp file then `link()`ed into place (never half-written, never replaces a competing key). Empty/malformed keyfiles raise `StoreKeyError` with recovery steps instead of being regenerated.
- Smoke now covers: 0644 keyfile rejected, empty keyfile not regenerated, 8 concurrent first-time key inits agree, 6 threads x 10 concurrent saves with different payload sizes leave one valid record and no stray temp files.
- Verification: `make check` green (5 smoke scripts, ~5 s).
- Remaining LOW: record format/embedder version field; key rotation changes filenames.
