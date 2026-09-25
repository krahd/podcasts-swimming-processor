from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from .audio import process_episode
from .catalog import Episode

MANIFEST_NAME = ".podcasts-swimming-processor.json"
PREFIX = "PSP_"
MANIFEST_VERSION = 1
SAFE_RE = re.compile(r"[^A-Za-z0-9._ -]+")


class ManifestError(RuntimeError):
    pass


def sanitize(text: str, max_len: int = 52) -> str:
    text = SAFE_RE.sub(" ", text).strip(" ._-")
    text = re.sub(r"\s+", " ", text)
    return (text[:max_len].rstrip(" ._-") or "untitled")


def device_filename_base(episode: Episode, preset_name: str, segment_minutes: int) -> str:
    date = (episode.published_at or "00000000")[:10].replace("-", "")
    signature = f"{episode.source_size}:{episode.source_mtime_ns}:{preset_name}:{segment_minutes}"
    fingerprint = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:8]
    return (
        f"{PREFIX}{date}_{sanitize(episode.show, 32)}_"
        f"{sanitize(episode.title, 54)}_{episode.uuid[:8]}_{fingerprint}"
    )


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
    return payload


def _atomic_manifest(device: Path, manifest: dict) -> None:
    path = _manifest_path(device)
    tmp = device / (MANIFEST_NAME + ".tmp")
    data = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _owned_path(device: Path, name: str) -> Path:
    if not isinstance(name, str) or not name.startswith(PREFIX) or Path(name).name != name:
        raise ManifestError(f"Unsafe managed filename in manifest: {name!r}")
    target = device / name
    if target.parent != device:
        raise ManifestError(f"Managed path escaped device root: {name!r}")
    return target


def _remove_owned(device: Path, names: list[str]) -> None:
    for name in names:
        target = _owned_path(device, name)
        if target.exists():
            target.unlink()


def validate_manifest_paths(device: Path, manifest: dict) -> None:
    for entry in manifest.get("episodes", {}).values():
        if not isinstance(entry, dict):
            raise ManifestError("Managed-device manifest has a malformed episode entry")
        for item in entry.get("files", []):
            if not isinstance(item, dict) or "name" not in item:
                raise ManifestError("Managed-device manifest has a malformed file entry")
            _owned_path(device, item["name"])
    pending = manifest.get("pending")
    if isinstance(pending, dict):
        for name in pending.get("files", []) or []:
            _owned_path(device, name)
        for name in pending.get("old_files", []) or []:
            _owned_path(device, name)
        new_entry = pending.get("new_entry")
        if isinstance(new_entry, dict):
            for item in new_entry.get("files", []) or []:
                if not isinstance(item, dict) or "name" not in item:
                    raise ManifestError("Pending transaction has a malformed file entry")
                _owned_path(device, item["name"])


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
        complete = bool(new_files) and all(
            _owned_path(device, item["name"]).is_file()
            and _owned_path(device, item["name"]).stat().st_size == int(item["size"])
            for item in new_files
            if isinstance(item, dict) and "name" in item and "size" in item
        ) and len(new_files) == sum(1 for item in new_files if isinstance(item, dict) and "name" in item and "size" in item)
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
                    partial = device / (item["name"] + ".partial")
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
        "source_size": episode.source_size,
        "source_mtime_ns": episode.source_mtime_ns,
        "preset": preset_name,
        "segment_minutes": segment_minutes,
    }


def _entry_current(device: Path, entry: dict, signature: dict) -> bool:
    if any(entry.get(k) != v for k, v in signature.items()):
        return False
    files = entry.get("files")
    if not isinstance(files, list) or not files:
        return False
    for item in files:
        if not isinstance(item, dict) or "name" not in item or "size" not in item:
            return False
        target = _owned_path(device, item["name"])
        if not target.is_file() or target.stat().st_size != int(item["size"]):
            return False
    return True


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
    if segment_minutes not in {0, 5, 10, 15}:
        raise ValueError("segment_minutes must be one of 0, 5, 10, 15")
    selected = list(dict.fromkeys(selected_uuids))
    unknown = [uuid for uuid in selected if uuid not in catalog]
    if unknown:
        raise ValueError(f"Unknown/non-downloaded episode UUID(s): {', '.join(unknown[:3])}")

    def emit(**kwargs):
        if progress:
            progress(kwargs)

    manifest = load_manifest(device)
    validate_manifest_paths(device, manifest)
    manifest = recover_pending(device, manifest)
    current = manifest["episodes"]
    selected_set = set(selected)

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
        episode = catalog[uuid]
        signature = _processing_signature(episode, preset_name, segment_minutes)
        existing = manifest["episodes"].get(uuid)
        if existing and _entry_current(device, existing, signature):
            emit(phase="kept", episode_uuid=uuid, index=index, total=total, message=f"Already current: {episode.title}")
            continue

        emit(phase="processing", episode_uuid=uuid, index=index, total=total, message=f"Processing: {episode.title}")
        with tempfile.TemporaryDirectory(prefix="podcast-swim-audio-") as tmp:
            out_dir = Path(tmp)
            base = device_filename_base(episode, preset_name, segment_minutes)
            outputs = process_episode(Path(episode.source_path), out_dir, base, preset_name, segment_minutes)
            files = [{"name": p.name, "size": p.stat().st_size} for p in outputs]
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
            known_owned = {
                item.get("name")
                for owned_entry in manifest["episodes"].values()
                if isinstance(owned_entry, dict)
                for item in owned_entry.get("files", [])
                if isinstance(item, dict)
            }
            for item in files:
                target = _owned_path(device, item["name"])
                partial = device / (item["name"] + ".partial")
                if target.exists() and item["name"] not in known_owned:
                    raise ManifestError(f"Refusing to overwrite an untracked device file: {item['name']}")
                if partial.exists():
                    raise ManifestError(f"Refusing to overwrite an untracked partial file: {partial.name}")
            manifest["pending"] = {"type": "replace", "uuid": uuid, "new_entry": new_entry, "old_files": old_files}
            _atomic_manifest(device, manifest)

            emit(phase="copying", episode_uuid=uuid, index=index, total=total, message=f"Copying: {episode.title}")
            for source, item in zip(outputs, files):
                final = _owned_path(device, item["name"])
                partial = device / (item["name"] + ".partial")
                with source.open("rb") as src, partial.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
                if partial.stat().st_size != item["size"]:
                    raise RuntimeError(f"Short copy to device for {item['name']}")
                os.replace(partial, final)

            _remove_owned(device, old_files)
            manifest["episodes"][uuid] = new_entry
            manifest["pending"] = None
            _atomic_manifest(device, manifest)

    manifest["settings"] = {"preset": preset_name, "segment_minutes": segment_minutes}
    _atomic_manifest(device, manifest)
    emit(phase="complete", total=total, message=f"Sync complete: {total} selected episode(s)")
    return manifest
