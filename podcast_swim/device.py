from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from .audio import PRESETS, process_episode
from .catalog import Episode

MANIFEST_NAME = ".podcasts-swimming-processor.json"
PREFIX = "PSP_"
MANIFEST_VERSION = 1
PROCESSING_VERSION = 4
SAFE_RE = re.compile(r"[^A-Za-z0-9._ -]+")


class ManifestError(RuntimeError):
    pass


def sanitize(text: str, max_len: int = 52) -> str:
    text = SAFE_RE.sub(" ", text).strip(" ._-")
    text = re.sub(r"\s+", " ", text)
    return (text[:max_len].rstrip(" ._-") or "untitled")


def device_filename_base(
    episode: Episode,
    preset_name: str,
    segment_minutes: int,
    replacement_salt: str = "",
) -> str:
    date = (episode.published_at or "00000000")[:10].replace("-", "")
    signature = (
        f"v{PROCESSING_VERSION}:{episode.uuid}:{episode.source_size}:"
        f"{episode.source_mtime_ns}:{preset_name}:{segment_minutes}:{replacement_salt}"
    )
    fingerprint = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
    uuid_tag = sanitize(episode.uuid, 8)
    return (
        f"{PREFIX}{date}_{sanitize(episode.show, 32)}_"
        f"{sanitize(episode.title, 54)}_{uuid_tag}_{fingerprint}"
    )


def _replacement_salt(existing: dict | None) -> str:
    if not existing:
        return ""
    payload = json.dumps(existing, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _manifest_path(device: Path) -> Path:
    return device / MANIFEST_NAME


def empty_manifest() -> dict:
    return {"version": MANIFEST_VERSION, "episodes": {}, "pending": None, "settings": {"preset": "swim", "segment_minutes": 10}}


def load_manifest(device: Path) -> dict:
    path = _manifest_path(device)
    if not path.exists():
        return empty_manifest()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Managed-device manifest is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        raise ManifestError("Managed-device manifest has an unsupported format")
    if not isinstance(payload.get("episodes"), dict):
        raise ManifestError("Managed-device manifest has invalid episode state")
    if payload.get("pending") is not None and not isinstance(payload.get("pending"), dict):
        raise ManifestError("Managed-device manifest has invalid pending state")
    payload.setdefault("settings", {"preset": "swim", "segment_minutes": 10})
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise ManifestError("Managed-device manifest has invalid settings")
    preset_name = settings.get("preset", "swim")
    segment_minutes = settings.get("segment_minutes", 10)
    if preset_name not in PRESETS or type(segment_minutes) is not int or segment_minutes not in {0, 5, 10, 15}:
        raise ManifestError("Managed-device manifest has invalid settings")
    for uuid, entry in payload["episodes"].items():
        if not isinstance(uuid, str) or not uuid or not isinstance(entry, dict):
            raise ManifestError("Managed-device manifest has a malformed episode entry")
        files = entry.get("files", [])
        if not isinstance(files, list):
            raise ManifestError("Managed-device manifest has a malformed file list")
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise ManifestError("Managed-device manifest has a malformed file entry")
            size = item.get("size")
            if type(size) is not int or size < 0:
                raise ManifestError("Managed-device manifest has an invalid file size")
    return payload


def _sync_dir(path: Path) -> None:
    """Best-effort directory durability for removable filesystems."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_manifest(device: Path, manifest: dict) -> None:
    path = _manifest_path(device)
    tmp = device / (MANIFEST_NAME + ".tmp")
    data = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _sync_dir(device)


def _owned_path(device: Path, name: str) -> Path:
    if not isinstance(name, str) or not name.startswith(PREFIX) or Path(name).name != name:
        raise ManifestError(f"Unsafe managed filename in manifest: {name!r}")
    target = device / name
    if target.parent != device:
        raise ManifestError(f"Managed path escaped device root: {name!r}")
    return target


def _remove_owned(device: Path, names: list[str]) -> None:
    removed = False
    for name in names:
        target = _owned_path(device, name)
        if target.exists():
            target.unlink()
            removed = True
    if removed:
        _sync_dir(device)


def validate_manifest_paths(device: Path, manifest: dict) -> None:
    owners: dict[str, str] = {}
    episode_files: dict[str, set[str]] = {}
    for uuid, entry in manifest.get("episodes", {}).items():
        if not isinstance(uuid, str) or not isinstance(entry, dict):
            raise ManifestError("Managed-device manifest has a malformed episode entry")
        files = entry.get("files", [])
        if not isinstance(files, list):
            raise ManifestError("Managed-device manifest has a malformed file list")
        names: set[str] = set()
        for item in files:
            if not isinstance(item, dict) or "name" not in item:
                raise ManifestError("Managed-device manifest has a malformed file entry")
            name = item["name"]
            _owned_path(device, name)
            if name in names:
                raise ManifestError(f"Managed filename is duplicated within an episode: {name}")
            previous = owners.setdefault(name, uuid)
            if previous != uuid:
                raise ManifestError(f"Managed filename is claimed by multiple episodes: {name}")
            names.add(name)
        episode_files[uuid] = names

    pending = manifest.get("pending")
    if not isinstance(pending, dict):
        return
    kind = pending.get("type")
    uuid = pending.get("uuid")
    if kind not in {"remove", "replace"} or not isinstance(uuid, str):
        raise ManifestError("Pending transaction has an invalid type or episode UUID")
    committed = episode_files.get(uuid, set())
    if kind == "remove":
        files = pending.get("files")
        if not isinstance(files, list) or len(files) != len(set(files)):
            raise ManifestError("Pending remove transaction has malformed files")
        for name in files:
            _owned_path(device, name)
        if set(files) != committed:
            raise ManifestError("Pending remove transaction does not match committed ownership")
        return

    old_files = pending.get("old_files")
    new_entry = pending.get("new_entry")
    if not isinstance(old_files, list) or len(old_files) != len(set(old_files)):
        raise ManifestError("Pending replace transaction has malformed old files")
    for name in old_files:
        _owned_path(device, name)
    if set(old_files) != committed:
        raise ManifestError("Pending replace transaction does not match committed ownership")
    if not isinstance(new_entry, dict):
        raise ManifestError("Pending replace transaction is malformed")
    new_files = new_entry.get("files")
    if not isinstance(new_files, list) or not new_files:
        raise ManifestError("Pending replace transaction has no valid output files")
    seen_new: set[str] = set()
    for item in new_files:
        if not isinstance(item, dict) or "name" not in item:
            raise ManifestError("Pending transaction has a malformed file entry")
        name = item["name"]
        _owned_path(device, name)
        if name in seen_new:
            raise ManifestError(f"Pending replacement filename is duplicated: {name}")
        other_owner = owners.get(name)
        if other_owner is not None and other_owner != uuid:
            raise ManifestError(f"Pending replacement collides with another episode: {name}")
        seen_new.add(name)


def recover_pending(device: Path, manifest: dict) -> dict:
    pending = manifest.get("pending")
    if not pending:
        return manifest
    kind = pending.get("type")
    uuid = pending.get("uuid")
    if not isinstance(uuid, str):
        raise ManifestError("Pending transaction has no valid episode UUID")
    if kind == "remove":
        _remove_owned(device, list(pending.get("files") or []))
        manifest["episodes"].pop(uuid, None)
        manifest["pending"] = None
        _atomic_manifest(device, manifest)
        return manifest
    if kind == "replace":
        new_entry = pending.get("new_entry")
        old_files = list(pending.get("old_files") or [])
        if not isinstance(new_entry, dict):
            raise ManifestError("Pending replace transaction is malformed")
        new_files = list(new_entry.get("files") or [])
        complete = bool(new_files)
        new_names: list[str] = []
        for item in new_files:
            if not isinstance(item, dict) or "name" not in item or "size" not in item:
                raise ManifestError("Pending replace transaction has a malformed file entry")
            try:
                expected_size = int(item["size"])
            except (TypeError, ValueError) as exc:
                raise ManifestError("Pending replace transaction has an invalid file size") from exc
            name = item["name"]
            new_names.append(name)
            target = _owned_path(device, name)
            if not target.is_file() or target.stat().st_size != expected_size:
                complete = False
        overlap = set(old_files) & set(new_names)
        if overlap:
            # Legacy transactions could replace a committed path in place. We
            # cannot know whether an overlapping final is the old or new bytes,
            # so preserve it, remove only non-overlapping new artefacts/partials,
            # and leave the committed episode entry in place for a safe recheck.
            for item in new_files:
                name = item["name"]
                partial = _owned_path(device, name + ".partial")
                partial.unlink(missing_ok=True)
                if name not in overlap:
                    _owned_path(device, name).unlink(missing_ok=True)
            manifest["pending"] = None
            _atomic_manifest(device, manifest)
            return manifest
        if complete:
            _remove_owned(device, old_files)
            manifest["episodes"][uuid] = new_entry
        else:
            # Roll back only files explicitly declared in the pending transaction;
            # the previously committed episode remains intact.
            for item in new_files:
                if isinstance(item, dict) and "name" in item:
                    target = _owned_path(device, item["name"])
                    if target.exists():
                        target.unlink()
                    partial = _owned_path(device, item["name"] + ".partial")
                    if partial.exists():
                        partial.unlink()
        manifest["pending"] = None
        _atomic_manifest(device, manifest)
        return manifest
    raise ManifestError(f"Unknown pending transaction type: {kind!r}")


def device_info(device: Path) -> dict:
    connected = device.is_dir()
    info = {"path": str(device), "connected": connected, "writable": False, "free_bytes": 0, "total_bytes": 0}
    if connected:
        info["writable"] = os.access(device, os.W_OK)
        try:
            usage = shutil.disk_usage(device)
            info["free_bytes"] = usage.free
            info["total_bytes"] = usage.total
        except OSError:
            pass
    return info


def _processing_signature(episode: Episode, preset_name: str, segment_minutes: int) -> dict:
    return {
        "processing_version": PROCESSING_VERSION,
        "source_size": episode.source_size,
        "source_mtime_ns": episode.source_mtime_ns,
        "preset": preset_name,
        "segment_minutes": segment_minutes,
    }


def _entry_files_valid(device: Path, entry: dict) -> bool:
    files = entry.get("files")
    if not isinstance(files, list) or not files:
        return False
    for item in files:
        if not isinstance(item, dict) or "name" not in item or "size" not in item:
            return False
        target = _owned_path(device, item["name"])
        try:
            expected_size = int(item["size"])
        except (TypeError, ValueError):
            return False
        if not target.is_file() or target.stat().st_size != expected_size:
            return False
    return True


def _entry_current(device: Path, entry: dict, signature: dict) -> bool:
    if any(entry.get(k) != v for k, v in signature.items()):
        return False
    return _entry_files_valid(device, entry)


def reconcile(
    device: Path,
    catalog: dict[str, Episode],
    selected_uuids: list[str],
    preset_name: str = "swim",
    segment_minutes: int = 10,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    if not device.is_dir() or not os.access(device, os.W_OK):
        raise RuntimeError(f"Device is not mounted writable at {device}")
    if preset_name not in PRESETS:
        raise ValueError(f"Unknown audio preset: {preset_name}")
    if type(segment_minutes) is not int or segment_minutes not in {0, 5, 10, 15}:
        raise ValueError("segment_minutes must be one of 0, 5, 10, 15")
    selected = list(dict.fromkeys(selected_uuids))

    def emit(**kwargs):
        if progress:
            progress(kwargs)

    manifest = load_manifest(device)
    validate_manifest_paths(device, manifest)
    manifest = recover_pending(device, manifest)
    current = manifest["episodes"]
    selected_set = set(selected)

    # A managed episode may no longer be downloaded in Apple Podcasts. It can
    # safely stay on the device as long as its existing files are intact and
    # its processing settings do not need to change. This also lets the UI
    # expose it so the user can untick and delete it.
    missing_sources = [uuid for uuid in selected if uuid not in catalog]
    for uuid in missing_sources:
        entry = current.get(uuid)
        if not isinstance(entry, dict):
            raise ValueError(f"Episode {uuid} is not downloaded and is not already managed on the device")
        if entry.get("preset") != preset_name or entry.get("segment_minutes") != segment_minutes:
            raise ValueError(
                f"{entry.get('title', uuid)} is no longer downloaded in Apple Podcasts; "
                "redownload it or untick it before changing processing settings"
            )
        if not _entry_files_valid(device, entry):
            raise ValueError(
                f"{entry.get('title', uuid)} is no longer downloaded and its managed device files are incomplete"
            )

    # Removals first: each transaction is journalled before deletion.
    for uuid in sorted(set(current) - selected_set):
        entry = current.get(uuid) or {}
        files = [item.get("name") for item in entry.get("files", []) if isinstance(item, dict) and item.get("name")]
        emit(phase="removing", episode_uuid=uuid, message="Removing unselected episode")
        manifest["pending"] = {"type": "remove", "uuid": uuid, "files": files}
        _atomic_manifest(device, manifest)
        _remove_owned(device, files)
        manifest["episodes"].pop(uuid, None)
        manifest["pending"] = None
        _atomic_manifest(device, manifest)

    total = len(selected)
    for index, uuid in enumerate(selected, start=1):
        if uuid not in catalog:
            entry = manifest["episodes"][uuid]
            emit(phase="kept", episode_uuid=uuid, index=index, total=total, message=f"Kept on device (source no longer downloaded): {entry.get('title', uuid)}")
            continue
        episode = catalog[uuid]
        signature = _processing_signature(episode, preset_name, segment_minutes)
        existing = manifest["episodes"].get(uuid)
        if existing and _entry_current(device, existing, signature):
            emit(phase="kept", episode_uuid=uuid, index=index, total=total, message=f"Already current: {episode.title}")
            continue

        initial_overall = ((index - 1) / total) if total else 0.0
        emit(
            phase="processing", episode_uuid=uuid, index=index, total=total,
            episode_progress=0.0, overall_progress=initial_overall,
            message=f"Processing: {episode.title}",
        )
        with tempfile.TemporaryDirectory(prefix="podcast-swim-audio-") as tmp:
            out_dir = Path(tmp)
            base = device_filename_base(
                episode, preset_name, segment_minutes, _replacement_salt(existing)
            )
            def audio_progress(info: dict) -> None:
                fraction = max(0.0, min(1.0, float(info.get("fraction", 0.0))))
                overall = ((index - 1) + fraction) / total if total else fraction
                emit(
                    phase="processing", episode_uuid=uuid, index=index, total=total,
                    episode_progress=fraction, overall_progress=overall,
                    processed_seconds=info.get("processed_seconds"),
                    duration_seconds=info.get("duration_seconds"),
                    eta_seconds=info.get("eta_seconds"),
                    message=f"Processing: {episode.title}",
                )
            outputs = process_episode(
                Path(episode.source_path), out_dir, base, preset_name, segment_minutes,
                progress=audio_progress,
            )
            files = [{"name": p.name, "size": p.stat().st_size} for p in outputs]
            names = [item["name"] for item in files]
            if len(names) != len(set(names)):
                raise RuntimeError("Audio processor produced duplicate output filenames")
            needed = sum(item["size"] for item in files)
            free = shutil.disk_usage(device).free
            if needed + 8 * 1024 * 1024 > free:
                raise RuntimeError(f"Not enough free space on device for {episode.title}")
            new_entry = {
                "uuid": uuid,
                "title": episode.title,
                "show": episode.show,
                **signature,
                "files": files,
            }
            old_files = []
            if existing:
                old_files = [item.get("name") for item in existing.get("files", []) if isinstance(item, dict) and item.get("name")]
            overlap = set(names) & set(old_files)
            if overlap:
                raise ManifestError(
                    "Refusing an unsafe in-place replacement of managed output: "
                    + ", ".join(sorted(overlap))
                )
            owned_by: dict[str, str] = {}
            for owner_uuid, owned_entry in manifest["episodes"].items():
                if not isinstance(owned_entry, dict):
                    continue
                for owned_item in owned_entry.get("files", []):
                    if isinstance(owned_item, dict) and isinstance(owned_item.get("name"), str):
                        name = owned_item["name"]
                        previous_owner = owned_by.setdefault(name, owner_uuid)
                        if previous_owner != owner_uuid:
                            raise ManifestError(f"Managed filename is claimed by multiple episodes: {name}")
            for item in files:
                target = _owned_path(device, item["name"])
                partial = _owned_path(device, item["name"] + ".partial")
                owner = owned_by.get(item["name"])
                if target.exists() and owner is None:
                    raise ManifestError(f"Refusing to overwrite an untracked device file: {item['name']}")
                if target.exists() and owner != uuid:
                    raise ManifestError(f"Refusing to overwrite a file managed by another episode: {item['name']}")
                if partial.exists():
                    raise ManifestError(f"Refusing to overwrite an untracked partial file: {partial.name}")
            manifest["pending"] = {"type": "replace", "uuid": uuid, "new_entry": new_entry, "old_files": old_files}
            _atomic_manifest(device, manifest)

            emit(phase="copying", episode_uuid=uuid, index=index, total=total, message=f"Copying: {episode.title}")
            for source, item in zip(outputs, files):
                final = _owned_path(device, item["name"])
                partial = _owned_path(device, item["name"] + ".partial")
                with source.open("rb") as src, partial.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
                if partial.stat().st_size != item["size"]:
                    raise RuntimeError(f"Short copy to device for {item['name']}")
                os.replace(partial, final)
            _sync_dir(device)

            _remove_owned(device, old_files)
            manifest["episodes"][uuid] = new_entry
            manifest["pending"] = None
            _atomic_manifest(device, manifest)

    manifest["settings"] = {"preset": preset_name, "segment_minutes": segment_minutes}
    _atomic_manifest(device, manifest)
    emit(phase="complete", total=total, message=f"Sync complete: {total} selected episode(s)")
    return manifest
