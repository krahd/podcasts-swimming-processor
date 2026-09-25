# Adversarial audit

The September 2026 hardening pass reviewed catalogue snapshot consistency, audio tail handling, managed-file ownership, filename collisions, interrupted transactions, concurrent sync admission, preview concurrency, token exposure, and regression coverage.

Material fixes include SQLite backup-based snapshots (including WAL state), UUID-qualified output fingerprints, processing-version migration for downloaded episodes, strict managed-file ownership collision refusal, malformed pending/settings fail-closed behaviour, no data-dropping on ffprobe failure, atomic sync admission, serialized preview generation, shorter status locks, token removal from browser history/API query strings, and expanded regression tests.
