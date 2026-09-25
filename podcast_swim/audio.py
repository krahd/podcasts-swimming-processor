from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioPreset:
    name: str
    description: str
    compressor_threshold: float
    compressor_ratio: float
    attack_ms: float
    release_ms: float
    loudness_lufs: float
    lra: float
    true_peak_db: float

    @property
    def filter_chain(self) -> str:
        return (
            f"acompressor=threshold={self.compressor_threshold}:ratio={self.compressor_ratio}:"
            f"attack={self.attack_ms}:release={self.release_ms},"
            f"loudnorm=I={self.loudness_lufs}:LRA={self.lra}:TP={self.true_peak_db}"
        )


PRESETS: dict[str, AudioPreset] = {
    "normal": AudioPreset(
        "normal", "Light compression; conventional podcast loudness", 0.10, 2.0, 20, 250, -16, 8, -1.5
    ),
    "swim": AudioPreset(
        "swim", "Moderate compression and extra loudness for underwater listening", 0.0631, 3.0, 10, 200, -14, 5, -1.0
    ),
    "aggressive": AudioPreset(
        "aggressive", "Strong compression and high loudness for especially quiet playback", 0.0398, 4.0, 5, 180, -12, 4, -1.0
    ),
}


def find_ffmpeg() -> str:
    candidates = [shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("FFmpeg is required. Install it with: brew install ffmpeg")


def find_ffprobe() -> str:
    candidates = [shutil.which("ffprobe"), "/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("ffprobe is required (it is installed with FFmpeg).")


def preset(name: str) -> AudioPreset:
    try:
        return PRESETS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown audio preset: {name}") from exc


def probe_duration(source: Path) -> float:
    result = subprocess.run(
        [find_ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    payload = json.loads(result.stdout)
    return float(payload.get("format", {}).get("duration") or 0)


def _run(command: list[str], timeout: int) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        tail = (result.stderr or result.stdout or "")[-4000:]
        raise RuntimeError(f"FFmpeg failed ({result.returncode}):\n{tail}")



def _merge_tiny_tail(outputs: list[Path], segment_seconds: float) -> list[Path]:
    """Avoid a nearly-empty final track caused by encoder/packet rounding."""
    if len(outputs) < 2:
        return outputs
    try:
        tail_duration = probe_duration(outputs[-1])
    except Exception:
        # Segment muxing can leave a header-only tail when the source ends
        # exactly on a boundary. It contains no playable audio.
        outputs[-1].unlink(missing_ok=True)
        return outputs[:-1]
    threshold = min(30.0, max(2.0, segment_seconds * 0.10))
    if tail_duration >= threshold:
        return outputs
    previous, tail = outputs[-2], outputs[-1]
    concat_file = previous.parent / ".concat-tail.txt"
    merged = previous.parent / (previous.stem + ".merged.mp3")
    def quote(path: Path) -> str:
        return str(path).replace("'", "'\\''")
    concat_file.write_text(f"file '{quote(previous)}'\nfile '{quote(tail)}'\n", encoding="utf-8")
    try:
        _run([find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(merged)], timeout=120)
        if not merged.is_file() or merged.stat().st_size <= previous.stat().st_size:
            return outputs
        merged.replace(previous)
        tail.unlink()
        return outputs[:-1]
    finally:
        concat_file.unlink(missing_ok=True)
        merged.unlink(missing_ok=True)

def process_episode(
    source: Path,
    output_dir: Path,
    base_name: str,
    preset_name: str = "swim",
    segment_minutes: int = 10,
    bitrate: str = "128k",
) -> list[Path]:
    if not source.is_file():
        raise FileNotFoundError(source)
    p = preset(preset_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg()
    common = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-af",
        p.filter_chain,
        "-codec:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        "-ar",
        "44100",
    ]
    if segment_minutes > 0:
        pattern = output_dir / f"{base_name}_p%03d.mp3"
        command = common + [
            "-f",
            "segment",
            "-segment_time",
            str(int(segment_minutes * 60)),
            "-segment_start_number",
            "1",
            "-reset_timestamps",
            "1",
            str(pattern),
        ]
    else:
        pattern = output_dir / f"{base_name}.mp3"
        command = common + [str(pattern)]
    # Real podcast episodes can be long; FFmpeg is local and bounded by 3h.
    _run(command, timeout=3 * 60 * 60)
    if segment_minutes > 0:
        outputs = sorted(output_dir.glob(f"{base_name}_p*.mp3"))
        outputs = _merge_tiny_tail(outputs, float(segment_minutes) * 60.0)
    else:
        outputs = [pattern] if pattern.exists() else []
    if not outputs or any(path.stat().st_size <= 0 for path in outputs):
        raise RuntimeError("FFmpeg produced no valid output tracks")
    return outputs


def render_preview(
    source: Path,
    dest: Path,
    preset_name: str = "swim",
    seconds: int = 30,
    start_seconds: float | None = None,
) -> Path:
    p = preset(preset_name)
    duration = probe_duration(source)
    if start_seconds is None:
        start_seconds = min(60.0, max(0.0, duration * 0.2))
    start_seconds = max(0.0, min(float(start_seconds), max(0.0, duration - 1)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = [
        find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start_seconds:.3f}", "-i", str(source), "-t", str(int(seconds)),
        "-vn", "-af", p.filter_chain, "-codec:a", "libmp3lame", "-b:a", "128k",
        "-ar", "44100", str(dest),
    ]
    _run(command, timeout=120)
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise RuntimeError("Preview generation failed")
    return dest


def estimate_output_bytes(duration_seconds: float, bitrate_kbps: int = 128) -> int:
    return int(max(0.0, duration_seconds) * bitrate_kbps * 1000 / 8 * 1.05)
