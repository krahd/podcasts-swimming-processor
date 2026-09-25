# Plan

## Objective
Build a dependency-light local macOS web application that lets a user choose downloaded Apple Podcasts episodes for a Mojawa Run Plus, preprocesses selected episodes for underwater spoken-word intelligibility, splits them into navigable tracks, safely reconciles the device to the selected set, and removes all app-managed chunks when an episode is unselected.

## Canonical sources and destinations
- Canonical repository: `https://github.com/krahd/podcasts-swimming-processor`
- Mac checkout: `/Users/tom/tom-repos/projects/podcasts-swimming-processor`
- Apple Podcasts database: `~/Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Documents/MTLibrary.sqlite`
- Apple Podcasts downloaded media cache: sibling group-container `Library/Cache/`
- Default device: `/Volumes/RUN PLUS`
- Audio runtime: Homebrew FFmpeg/ffprobe.

## Scope
1. Read downloaded Apple Podcasts metadata without modifying Apple Podcasts.
2. Localhost-only web UI with search, episode checkboxes, synced state, audio preset, segment length, preview, sync progress, and safe eject.
3. Default Swim processing: moderate speech compression, loudness normalisation, peak safety, MP3 encoding, then 10-minute segmentation.
4. Presets: Normal, Swim, Aggressive; configurable 5/10/15-minute or whole-episode output.
5. Checked episodes become app-managed files on the Mojawa; unchecked managed episodes have all their generated chunks removed.
6. Never delete or overwrite unrelated files on the device.
7. Manifest-driven, interruption-safe reconciliation and atomic manifest updates.
8. Standard-library Python web server + vanilla HTML/CSS/JS; FFmpeg is the only external runtime dependency.
9. Unit/integration tests using temporary mock device and synthetic audio; live-device reads only until explicit sync from the UI.
10. Documentation and one-command launcher.

## Out of scope
- Writing to the Apple Podcasts database or changing Apple Podcasts play state.
- Network podcast downloading or subscription management.
- Deleting arbitrary files from the Mojawa.
- Remote/web-hosted service.

## Phases and gates
1. **Environment/schema grounding** — verify actual Mac paths, database schema, FFmpeg, and Mojawa mount. Gate: concrete paths/schema documented.
2. **Core model** — catalog reader, media path resolution, presets, processor, managed manifest/device reconciliation. Gate: unit tests for path safety, manifest corruption, add/update/remove, and interrupted copies.
3. **Web UI** — local authenticated API, selection UI, filters, settings, preview, progress, eject. Gate: API/UI integration tests and manual localhost smoke test.
4. **Audio validation** — synthetic and at least one locally downloaded episode preview processed by each preset; verify durations, segmentation, and peak/loudness behaviour with ffprobe/ffmpeg analysis. Do not copy live episode output to the device during automated tests.
5. **Live-device smoke test** — app detects RUN PLUS and reads free space without mutating it; user-triggered sync is the only live mutation path.
6. **Documentation/packaging** — README, launcher, troubleshooting, privacy/safety notes.
7. **Final audit and integration** — full tests, adversarial safety review, workspace integration to main, push, and independent remote verification.

## Safety and recovery
- Bind HTTP server to `127.0.0.1` only and protect mutating API calls with a per-process random token.
- Resolve all device paths against the mounted device root; reject traversal/absolute managed paths.
- Delete only files listed in the app manifest and matching the app filename prefix.
- Fail closed on missing/corrupt manifest instead of guessing ownership.
- Generate audio in a Mac temp/cache directory; copy via `.partial` then rename on the device.
- Write manifest to a temporary sibling then atomic replace after successful reconciliation.
- Preserve existing managed episode files until replacement output is fully copied.
- On disconnect or FFmpeg failure, report error and preserve the last valid manifest.

## Final acceptance criteria
- UI displays real downloaded Apple Podcasts episodes.
- Selection state round-trips through manifest.
- Selected episodes process with the requested preset and default 10-minute chunks.
- Unselecting removes every managed chunk for that episode and nothing else.
- Preview works without device mutation.
- All tests pass, README instructions are reproducible on the current Mac, and the live RUN PLUS device is detected.
- GitHub main contains the verified implementation and `STATUS.md` says COMPLETE.
