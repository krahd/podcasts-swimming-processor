# Podcast Swimming Processor

A small local macOS web application for choosing downloaded Apple Podcasts episodes and preparing them for a **Mojawa Run Plus** (or another storage-based swimming player).

The problem it solves is specific: swimming headphones often have only **previous/next track** controls and can sound too quiet for spoken-word audio underwater. This application turns selected podcast episodes into louder, dynamically controlled, navigable MP3 tracks and keeps the device in sync with the selection you make in the browser.

## What it does

- Reads **downloaded** episodes from Apple Podcasts on the Mac. It never writes to Apple Podcasts or changes its playback state.
- Opens a browser interface on `127.0.0.1` where you can search and tick individual episodes.
- Processes selected episodes with speech-oriented dynamic-range compression and loudness normalisation.
- Splits them into **10-minute tracks by default**, so previous/next acts approximately like ±10-minute navigation.
- Copies the processed tracks to the Mojawa Run Plus.
- When you untick a previously synced episode and apply the selection, removes **all generated tracks for that episode**.
- Tracks its own files in a manifest and will not delete unrelated music or files from the device.
- Includes 30-second processed previews, selectable loudness presets, progress reporting, and safe eject.

## Requirements

- macOS with Apple Podcasts.
- Episodes you want to choose must first be downloaded in Apple Podcasts.
- Python 3 (the launcher uses Homebrew Python at `/opt/homebrew/bin/python3`).
- FFmpeg and ffprobe. Install with:

```sh
brew install ffmpeg
```

- Mojawa Run Plus mounted as `/Volumes/RUN PLUS`. A different mounted volume can be supplied with `--device`.

There are **no pip dependencies**. The web server uses Python's standard library and the interface is plain HTML/CSS/JavaScript.

## Run it

Plug the Mojawa into the Mac and wait for `RUN PLUS` to appear in Finder. Then either double-click:

```text
run.command
```

or in Terminal:

```sh
cd /path/to/podcasts-swimming-processor
./run.command
```

The application opens a local browser page automatically. Stop the server with `Ctrl-C` in Terminal.

For another device path:

```sh
./run.command --device "/Volumes/OTHER PLAYER"
```

## Using the interface

1. Search or browse your downloaded Apple Podcasts episodes.
2. Tick the episodes you want on the player. Episodes already managed by the application are initially ticked and labelled **on device**.
3. Choose an audio preset and track length.
4. Optionally use **Preview** to hear a 30-second processed sample on the Mac.
5. Press **Apply selection**.
6. When processing and copying finishes, use **Eject** before unplugging the player.

Unticking an episode and applying the selection deletes the application's generated pieces for that episode. Unrelated files on the player are outside the application's ownership and are left alone.

## Audio presets

Processing is applied to the complete episode **before** segmentation, so adjacent tracks remain acoustically consistent.

- **Normal** — light compression, target approximately -16 LUFS.
- **Swim** — default; moderate compression, reduced loudness range, target approximately -14 LUFS.
- **Aggressive** — stronger compression and approximately -12 LUFS for unusually quiet playback.

FFmpeg's `loudnorm` stage also constrains true peaks. The intent is improved spoken-word audibility, not simply adding uncontrolled gain.

Track lengths are 5, 10, 15 minutes, or whole episode. Ten minutes is the default because the Run Plus has track navigation but no intra-track rewind/fast-forward.

## Safety model

The application is deliberately conservative about the removable device:

- It only binds the server to localhost and uses a random per-run API token.
- It writes generated audio with the `PSP_` prefix and records exact filenames/sizes in `.podcasts-swimming-processor.json` on the device.
- It only deletes filenames recorded in that manifest and passing strict path/prefix validation.
- A corrupt or unsupported manifest disables synchronisation rather than guessing which files belong to the application.
- Audio is processed on the Mac first. Device copies go through `.partial` files and a journalled manifest transaction.
- If a copy is interrupted, the next sync recovers or rolls back only files explicitly named by the pending transaction.
- Changes to processing settings/source media use a new fingerprinted filename, so the previous valid tracks remain recoverable until replacement tracks are complete.

The application never writes to Apple's `MTLibrary.sqlite` database.

## Apple Podcasts discovery

Current Apple Podcasts stores metadata at:

```text
~/Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Documents/MTLibrary.sqlite
```

and downloaded episode media as UUID-named files in the group container's `Library/Cache/` directory. The application enumerates those root-level media files and joins their UUIDs to a disposable snapshot of the Podcasts database. It deliberately ignores `Assets/StreamedMedia`, which may contain streamed rather than explicitly downloaded material.

## Tests

```sh
/opt/homebrew/bin/python3 -m unittest discover -s tests -v
```

The tests use temporary mock devices and synthetic audio. Automated tests do **not** copy podcast audio to the real Mojawa.

## Repository

Canonical repository: <https://github.com/krahd/podcasts-swimming-processor>
