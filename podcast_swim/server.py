from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .audio import PRESETS, render_preview
from .catalog import Episode, load_downloaded_episodes
from .device import ManifestError, device_info, load_manifest, reconcile, validate_manifest_paths

DEFAULT_DEVICE = Path("/Volumes/RUN PLUS")
WEB_ROOT = Path(__file__).parent / "web"


@dataclass
class RuntimeState:
    device: Path
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    lock: threading.RLock = field(default_factory=threading.RLock)
    preview_lock: threading.Lock = field(default_factory=threading.Lock)
    sync_status: dict = field(default_factory=lambda: {"running": False, "phase": "idle", "message": "Ready"})
    preview_dir: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="podcast-swim-preview-")))
    catalog_cache: dict[str, Episode] = field(default_factory=dict)

    def refresh_catalog(self) -> dict[str, Episode]:
        episodes = load_downloaded_episodes()
        with self.lock:
            self.catalog_cache = {e.uuid: e for e in episodes}
        return self.catalog_cache

    def status_update(self, payload: dict) -> None:
        with self.lock:
            self.sync_status.update(payload)
            self.sync_status["running"] = payload.get("phase") not in {"complete", "error", "idle"}
            if payload.get("phase") in {"starting", "complete"}:
                self.sync_status.pop("error", None)

    def begin_sync(self) -> bool:
        with self.lock:
            if self.sync_status.get("running"):
                return False
            self.sync_status = {"running": True, "phase": "starting", "message": "Starting sync"}
            return True

    def begin_eject(self) -> bool:
        with self.lock:
            if self.sync_status.get("running"):
                return False
            self.sync_status = {"running": True, "phase": "ejecting", "message": "Ejecting device"}
            return True

    def status_snapshot(self) -> dict:
        with self.lock:
            return dict(self.sync_status)


class Handler(BaseHTTPRequestHandler):
    server_version = "PodcastSwim/0.1"

    @property
    def app(self) -> RuntimeState:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        print(f"[web] {self.address_string()} - {fmt % args}")

    def _token(self) -> str:
        header = self.headers.get("X-PSP-Token", "")
        if header:
            return header
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/preview/"):
            query = urllib.parse.parse_qs(parsed.query)
            return query.get("token", [""])[0]
        return ""

    def _authorized(self) -> bool:
        return secrets.compare_digest(self._token(), self.app.token)

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid Content-Length")
        if length <= 0 or length > 1024 * 1024:
            raise ValueError("Invalid request size")
        raw = self.rfile.read(length)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _send_file(self, path: Path, content_type: str | None = None):
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        resolved_type = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", resolved_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if resolved_type.startswith("text/html"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self'; media-src 'self'; "
                "img-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'none'",
            )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
            return
        if not self._authorized():
            self._json({"error": "Unauthorized local session"}, 403)
            return
        if parsed.path == "/api/state":
            try:
                catalog = self.app.refresh_catalog()
                manifest_error = None
                try:
                    manifest = load_manifest(self.app.device)
                    validate_manifest_paths(self.app.device, manifest)
                except ManifestError as exc:
                    manifest = {"episodes": {}, "pending": None}
                    manifest_error = str(exc)
                pending_recovery = bool(manifest.get("pending"))
                synced = set(manifest.get("episodes", {}))
                episode_rows = [{**e.json(), "synced": e.uuid in synced, "source_missing": False} for e in catalog.values()]
                for uuid in sorted(synced - set(catalog)):
                    entry = manifest.get("episodes", {}).get(uuid, {})
                    episode_rows.append({
                        "uuid": uuid,
                        "title": entry.get("title", uuid),
                        "show": entry.get("show", "Unknown Podcast"),
                        "author": "",
                        "published_at": None,
                        "duration_seconds": 0,
                        "source_path": "",
                        "source_size": 0,
                        "source_mtime_ns": 0,
                        "synced": True,
                        "source_missing": True,
                    })
                with self.app.lock:
                    status = dict(self.app.sync_status)
                self._json({
                    "device": device_info(self.app.device),
                    "episodes": episode_rows,
                    "selected": list(synced),
                    "presets": {key: {"name": val.name, "description": val.description, "lufs": val.loudness_lufs} for key, val in PRESETS.items()},
                    "defaults": manifest.get("settings", {"preset": "swim", "segment_minutes": 10}),
                    "status": status,
                    "manifest_error": manifest_error,
                    "pending_recovery": pending_recovery,
                })
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
            return
        if parsed.path == "/api/status":
            self._json(self.app.status_snapshot())
            return
        if parsed.path.startswith("/preview/"):
            name = Path(parsed.path.removeprefix("/preview/")).name
            if not name.endswith(".mp3"):
                self.send_error(404)
                return
            self._send_file(self.app.preview_dir / name, "audio/mpeg")
            return
        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self._authorized():
            self._json({"error": "Unauthorized local session"}, 403)
            return
        try:
            payload = self._read_json()
        except Exception as exc:
            self._json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/sync":
            selected = payload.get("selected")
            preset_name = payload.get("preset", "swim")
            segment_minutes = payload.get("segment_minutes", 10)
            if not isinstance(selected, list) or len(selected) > 5000 or not all(isinstance(x, str) for x in selected):
                self._json({"error": "selected must be a list of episode UUIDs"}, 400)
                return
            if preset_name not in PRESETS or segment_minutes not in {0, 5, 10, 15}:
                self._json({"error": "Invalid processing settings"}, 400)
                return
            if not self.app.begin_sync():
                self._json({"error": "A sync is already running"}, 409)
                return
            try:
                catalog = self.app.refresh_catalog()
            except Exception as exc:
                self.app.status_update({"phase": "error", "message": str(exc), "error": str(exc)})
                self._json({"error": str(exc)}, 500)
                return
            def worker():
                try:
                    reconcile(self.app.device, catalog, selected, preset_name, segment_minutes, self.app.status_update)
                except Exception as exc:
                    self.app.status_update({"running": False, "phase": "error", "message": str(exc), "error": str(exc)})
            thread = threading.Thread(target=worker, name="podcast-sync", daemon=True)
            thread.start()
            self._json({"started": True})
            return
        if parsed.path == "/api/preview":
            uuid = payload.get("uuid")
            preset_name = payload.get("preset", "swim")
            if not isinstance(uuid, str) or preset_name not in PRESETS:
                self._json({"error": "Invalid preview request"}, 400)
                return
            catalog = self.app.refresh_catalog()
            episode = catalog.get(uuid)
            if not episode:
                self._json({"error": "Episode is no longer downloaded"}, 404)
                return
            try:
                name = f"preview-{secrets.token_hex(12)}.mp3"
                with self.app.preview_lock:
                    render_preview(Path(episode.source_path), self.app.preview_dir / name, preset_name)
                    for stale in self.app.preview_dir.glob("preview-*.mp3"):
                        if stale.name == name:
                            continue
                        try:
                            stale.unlink()
                        except OSError:
                            pass
                self._json({"url": f"/preview/{name}?token={urllib.parse.quote(self.app.token)}"})
            except Exception as exc:
                self._json({"error": str(exc)}, 500)
            return
        if parsed.path == "/api/eject":
            if not self.app.begin_eject():
                self._json({"error": "Cannot eject while another device operation is running"}, 409)
                return
            try:
                result = subprocess.run(["/usr/sbin/diskutil", "eject", str(self.app.device)], capture_output=True, text=True, timeout=30)
                if result.returncode:
                    raise RuntimeError((result.stderr or result.stdout).strip())
                self.app.status_update({"phase": "complete", "message": "Device ejected"})
                self._json({"ejected": True, "message": result.stdout.strip()})
            except Exception as exc:
                self.app.status_update({"phase": "error", "message": str(exc), "error": str(exc)})
                self._json({"error": str(exc)}, 500)
            return
        self.send_error(404)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Prepare Apple Podcasts episodes for swimming headphones")
    parser.add_argument("--device", type=Path, default=DEFAULT_DEVICE, help="mounted swimming-headphone volume")
    parser.add_argument("--host", default="127.0.0.1", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="do not open the browser automatically")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("For safety this application only supports localhost binding")
    app = RuntimeState(device=args.device.expanduser())
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.app = app  # type: ignore[attr-defined]
    # URL fragments are not sent in HTTP requests. The UI consumes and
    # removes this bootstrap token immediately, then authenticates APIs by header.
    url = f"http://127.0.0.1:{server.server_port}/#token={urllib.parse.quote(app.token)}"
    print(f"Podcast Swimming Processor: {url}")
    print(f"Device: {app.device}")
    if not args.no_open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        shutil.rmtree(app.preview_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
