# Worklog

- 2026-09-24: User created canonical GitHub repository `krahd/podcasts-swimming-processor`.
- 2026-09-24: Verified Mojawa mounts as `/Volumes/RUN PLUS`; Apple Podcasts DB exists at the current macOS group-container path; Homebrew Python 3.14.7, FFmpeg 9.0.1, and ffprobe are installed; Flask/FastAPI/Jinja are not installed. Chose standard-library local server + vanilla browser UI to minimise dependencies.
- 2026-09-24: Verified Apple Podcasts schema includes `ZMTEPISODE`/`ZMTPODCAST`, episode UUID/download-path/title/duration fields, and UUID-named MP3 cache files.
- 2026-09-24: Audited design for device ownership, deletion safety, manifest corruption, interruption, local-server exposure, and audio-processing order. Repaired plan to use manifest-only deletion, path containment, `.partial` copies, fail-closed state, localhost token, and whole-episode processing before segmentation.
