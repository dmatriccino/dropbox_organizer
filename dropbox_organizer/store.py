"""SQLite checkpoint store for scan state and file metadata.

Everything here is a *local* cache. Nothing in this module talks to Dropbox.
Each page of results is committed in a single transaction together with the
cursor that produced it, so a crash can never leave the cursor ahead of (or
behind) the stored rows.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_state (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    cursor        TEXT,
    complete      INTEGER NOT NULL DEFAULT 0,
    pages         INTEGER NOT NULL DEFAULT 0,
    files_seen    INTEGER NOT NULL DEFAULT 0,
    excluded_dirs TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS media_files (
    id              TEXT PRIMARY KEY,
    path_display    TEXT NOT NULL,
    path_lower      TEXT NOT NULL,
    content_hash    TEXT,
    size_bytes      INTEGER NOT NULL,
    client_modified TEXT NOT NULL,
    server_modified TEXT NOT NULL,
    ext             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_hash ON media_files(content_hash);
CREATE INDEX IF NOT EXISTS idx_media_path ON media_files(path_lower);
CREATE TABLE IF NOT EXISTS excluded_media (
    id              TEXT PRIMARY KEY,
    path_display    TEXT NOT NULL,
    path_lower      TEXT NOT NULL,
    excluded_folder TEXT NOT NULL,
    ext             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_excluded_path ON excluded_media(path_lower);
"""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class MediaRow:
    id: str
    path_display: str
    path_lower: str
    content_hash: str | None
    size_bytes: int
    client_modified: str
    server_modified: str
    ext: str


@dataclass(frozen=True)
class ExcludedRow:
    id: str
    path_display: str
    path_lower: str
    excluded_folder: str
    ext: str


@dataclass(frozen=True)
class ScanState:
    cursor: str | None
    complete: bool
    pages: int
    files_seen: int
    excluded_dirs: tuple[str, ...]
    started_at: str
    updated_at: str


class ScanStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> ScanStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- state ---------------------------------------------------------------

    def load_state(self) -> ScanState | None:
        row = self._conn.execute(
            "SELECT cursor, complete, pages, files_seen, excluded_dirs, started_at, updated_at"
            " FROM scan_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return ScanState(
            cursor=row[0],
            complete=bool(row[1]),
            pages=row[2],
            files_seen=row[3],
            excluded_dirs=tuple(json.loads(row[4])),
            started_at=row[5],
            updated_at=row[6],
        )

    def reset(self, excluded_dirs: Iterable[str]) -> ScanState:
        """Wipe the local cache and start a fresh scan state."""
        now = _now()
        with self._conn:
            self._conn.execute("DELETE FROM media_files")
            self._conn.execute("DELETE FROM excluded_media")
            self._conn.execute("DELETE FROM scan_state")
            self._conn.execute(
                "INSERT INTO scan_state (id, cursor, complete, pages, files_seen, excluded_dirs,"
                " started_at, updated_at) VALUES (1, NULL, 0, 0, 0, ?, ?, ?)",
                (json.dumps(list(excluded_dirs)), now, now),
            )
        state = self.load_state()
        assert state is not None
        return state

    def commit_page(
        self,
        *,
        cursor: str,
        has_more: bool,
        files_seen: int,
        media: Iterable[MediaRow],
        excluded: Iterable[ExcludedRow],
        forgotten_paths: Iterable[str] = (),
    ) -> None:
        """Atomically store one page of results plus the cursor that follows it.

        ``forgotten_paths`` are paths Dropbox reported as deleted while the scan
        was in progress; they're dropped from this local cache only.
        """
        with self._conn:
            for p in forgotten_paths:
                for table in ("media_files", "excluded_media"):
                    self._conn.execute(
                        f"DELETE FROM {table}"
                        " WHERE path_lower = ? OR path_lower LIKE ? ESCAPE '\\'",
                        (p, _like_prefix(p)),
                    )
            for m in media:
                self._conn.execute("DELETE FROM excluded_media WHERE id = ?", (m.id,))
                self._conn.execute(
                    "INSERT OR REPLACE INTO media_files VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        m.id,
                        m.path_display,
                        m.path_lower,
                        m.content_hash,
                        m.size_bytes,
                        m.client_modified,
                        m.server_modified,
                        m.ext,
                    ),
                )
            for x in excluded:
                self._conn.execute("DELETE FROM media_files WHERE id = ?", (x.id,))
                self._conn.execute(
                    "INSERT OR REPLACE INTO excluded_media VALUES (?, ?, ?, ?, ?)",
                    (x.id, x.path_display, x.path_lower, x.excluded_folder, x.ext),
                )
            self._conn.execute(
                "UPDATE scan_state SET cursor = ?, complete = ?, pages = pages + 1,"
                " files_seen = files_seen + ?, updated_at = ? WHERE id = 1",
                (cursor, 0 if has_more else 1, files_seen, _now()),
            )

    # -- queries -------------------------------------------------------------

    def media_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM media_files").fetchone()[0]

    def excluded_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM excluded_media").fetchone()[0]

    def iter_media(self) -> Iterator[MediaRow]:
        cur = self._conn.execute(
            "SELECT id, path_display, path_lower, content_hash, size_bytes, client_modified,"
            " server_modified, ext FROM media_files ORDER BY path_lower"
        )
        for row in cur:
            yield MediaRow(*row)

    def excluded_by_ext(self) -> list[tuple[str, int]]:
        return list(
            self._conn.execute("SELECT ext, COUNT(*) FROM excluded_media GROUP BY ext ORDER BY ext")
        )

    def excluded_by_folder(self) -> list[tuple[str, int]]:
        return [
            (folder, count)
            for folder, count in self._conn.execute(
                "SELECT excluded_folder, COUNT(*) FROM excluded_media"
                " GROUP BY excluded_folder ORDER BY COUNT(*) DESC, excluded_folder"
            )
        ]


def _like_prefix(path_lower: str) -> str:
    escaped = path_lower.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped.rstrip("/") + "/%"
