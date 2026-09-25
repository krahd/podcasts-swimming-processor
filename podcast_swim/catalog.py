from __future__ import annotations

import contextlib
import datetime as dt
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

GROUP = Path.home() / "Library/Group Containers/243LU875E5.groups.com.apple.podcasts"
DEFAULT_DB = GROUP / "Documents/MTLibrary.sqlite"
DEFAULT_CACHE = GROUP / "Library/Cache"
SUPPORTED_MEDIA = {".mp3", ".m4a", ".aac", ".wav", ".flac"}
APPLE_EPOCH = 978307200.0


@dataclass(frozen=True)
class Episode:
    uuid: str
    title: str
    show: str
    author: str
    published_at: str | None
    duration_seconds: float
    source_path: str
    source_size: int
    source_mtime_ns: int

    def json(self) -> dict:
        return asdict(self)


def _iso_apple_timestamp(value: object) -> str | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number:
        return None
    # Core Data dates in Podcasts use the 2001 reference epoch. Be tolerant of
    # a future schema returning Unix seconds instead.
    unix = number + APPLE_EPOCH if number < 900_000_000 else number
    try:
        return dt.datetime.fromtimestamp(unix, tz=dt.timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _snapshot_db(db_path: Path) -> contextlib.AbstractContextManager[Path]:
    @contextlib.contextmanager
    def manager():
        with tempfile.TemporaryDirectory(prefix="podcast-swim-db-") as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "MTLibrary.sqlite"
            # Podcasts commonly has an active WAL. Copy the database and its
            # WAL/SHM siblings together, then open only the disposable copy.
            # Retry once if the first snapshot races a writer.
            last_error: Exception | None = None
            for _ in range(2):
                try:
                    shutil.copyfile(db_path, target)
                    for suffix in ("-wal", "-shm"):
                        src = Path(str(db_path) + suffix)
                        dst = Path(str(target) + suffix)
                        if src.exists():
                            shutil.copyfile(src, dst)
                        elif dst.exists():
                            dst.unlink()
                    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=1)
                    try:
                        conn.execute("SELECT 1").fetchone()
                    finally:
                        conn.close()
                    last_error = None
                    break
                except (OSError, sqlite3.Error) as exc:
                    last_error = exc
            if last_error:
                raise RuntimeError(f"Could not snapshot Apple Podcasts database: {last_error}")
            yield target
    return manager()


def _media_by_uuid(cache_root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if not cache_root.is_dir():
        return result
    # Root-level UUID files are Apple Podcasts' durable downloads. Deliberately
    # do not treat Assets/StreamedMedia as downloaded episodes.
    for child in cache_root.iterdir():
        if child.is_file() and child.suffix.lower() in SUPPORTED_MEDIA:
            result[child.stem.upper()] = child
    return result


def _chunks(items: list[str], size: int = 400) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def load_downloaded_episodes(
    db_path: Path = DEFAULT_DB,
    cache_root: Path = DEFAULT_CACHE,
) -> list[Episode]:
    media = _media_by_uuid(cache_root)
    if not media:
        return []
    if not db_path.is_file():
        raise FileNotFoundError(f"Apple Podcasts database not found: {db_path}")

    metadata: dict[str, sqlite3.Row] = {}
    with _snapshot_db(db_path) as snapshot:
        conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True, timeout=1)
        conn.row_factory = sqlite3.Row
        try:
            uuids = list(media)
            for block in _chunks(uuids):
                qs = ",".join("?" for _ in block)
                query = f"""
                    SELECT
                        e.ZUUID AS uuid,
                        COALESCE(NULLIF(e.ZCLEANEDTITLE,''), NULLIF(e.ZTITLE,''), e.ZUUID) AS title,
                        COALESCE(NULLIF(p.ZTITLE,''), NULLIF(e.ZAUTHOR,''), 'Unknown Podcast') AS show,
                        COALESCE(NULLIF(e.ZAUTHOR,''), NULLIF(p.ZAUTHOR,''), '') AS author,
                        e.ZPUBDATE AS published,
                        COALESCE(e.ZDURATION, 0) AS duration
                    FROM ZMTEPISODE e
                    LEFT JOIN ZMTPODCAST p ON p.Z_PK = e.ZPODCAST
                    WHERE UPPER(e.ZUUID) IN ({qs})
                """
                for row in conn.execute(query, block):
                    if row["uuid"]:
                        metadata[str(row["uuid"]).upper()] = row
        finally:
            conn.close()

    episodes: list[Episode] = []
    for key, path in media.items():
        row = metadata.get(key)
        stat = path.stat()
        if row:
            title = str(row["title"] or key)
            show = str(row["show"] or "Unknown Podcast")
            author = str(row["author"] or "")
            published = _iso_apple_timestamp(row["published"])
            duration = float(row["duration"] or 0)
            uuid = str(row["uuid"])
        else:
            title = path.stem
            show = "Unknown Podcast"
            author = ""
            published = None
            duration = 0.0
            uuid = path.stem
        episodes.append(
            Episode(
                uuid=uuid,
                title=title,
                show=show,
                author=author,
                published_at=published,
                duration_seconds=duration,
                source_path=str(path),
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
            )
        )

    episodes.sort(key=lambda e: (e.published_at or "", e.show, e.title), reverse=True)
    return episodes
