# Adversarial audit

The September 2026 hardening pass reviewed catalogue snapshot consistency, audio tail handling, managed-file ownership, filename collisions, interrupted transactions, concurrent sync admission, preview concurrency, token exposure, and regression coverage.

Material fixes include SQLite backup-based snapshots (including WAL state), UUID-qualified output fingerprints, processing-version migration for downloaded episodes, strict managed-file ownership collision refusal, malformed pending/settings fail-closed behaviour, no data-dropping on ffprobe failure, atomic sync admission, serialized preview generation, shorter status locks, token removal from browser history/API query strings, and expanded regression tests.

## Intensive second pass — 2026-09-25

The second pass reproduced a data-integrity defect in replacement processing: if an episode had to be regenerated without a source/settings fingerprint change, the new files reused the committed filenames and were then deleted as “old” files after copy. The same overlap made legacy interrupted replacement recovery ambiguous. The repair gives every replacement a generation-derived filename salt, includes the processing version in the fingerprint, explicitly rejects in-place replacement, and conservatively resolves legacy overlapping pending transactions without deleting the ambiguous final path.

Additional hardening validates audio presets and segment types before any destructive action, validates manifest episode/file schemas and ownership semantics, prevents pending transactions from claiming unrelated `PSP_` files, rejects duplicate ownership, sanitises UUID filename tags, fsyncs removable-directory metadata on best effort, checks SQLite snapshot integrity, handles duplicate Apple cache encodings deterministically, serialises eject against sync admission, exposes interrupted-sync recovery in the UI, moves the bootstrap token to a URL fragment, and limits query-string tokens to preview media requests. Regression coverage was expanded for each material defect.

### Final verification — intensive second pass

After the second adversarial repair pass, the complete automated suite passed (32/32), Python compilation and `git diff --check` passed, and the zsh launcher passed `zsh -n`. ShellCheck is not an applicable gate for `run.command` because the file is a zsh script and ShellCheck reports SC1071 for unsupported zsh syntax. A live read-only environment check confirmed 15 downloaded Apple Podcasts episodes and a connected writable `RUN PLUS`. No destructive live-device test was performed; device mutation remains user-triggered. All material defects found in this pass were repaired before integration.
