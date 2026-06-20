from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileRecord:
    path: str
    file_name: str
    content_hash: str
    size: int
    mtime_ns: int


class FileCatalog:
    """SQLite-backed content and path catalog.

    Connections are intentionally short-lived and never shared across threads.
    WAL mode allows watcher reads while the ingestion worker commits updates.
    """

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS content_objects (
                    content_hash TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    indexed_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    file_name TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    last_seen REAL NOT NULL,
                    FOREIGN KEY(content_hash)
                        REFERENCES content_objects(content_hash)
                        ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_files_content_hash
                    ON files(content_hash);
                """
            )

    def get_file(self, path: str) -> FileRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT path, file_name, content_hash, size, mtime_ns
                FROM files WHERE path = ?
                """,
                (path,),
            ).fetchone()
        return FileRecord(**dict(row)) if row else None

    def has_content(self, content_hash: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM content_objects WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
        return row is not None

    def all_paths(self) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT path FROM files").fetchall()
        return {str(row["path"]) for row in rows}

    def aliases(self, content_hash: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT file_name FROM files
                WHERE content_hash = ? ORDER BY file_name
                """,
                (content_hash,),
            ).fetchall()
        return [str(row["file_name"]) for row in rows]

    def content_hashes_for_paths(self, paths: Iterable[str]) -> set[str]:
        normalized_paths = list(dict.fromkeys(paths))
        if not normalized_paths:
            return set()
        placeholders = ",".join("?" for _ in normalized_paths)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT DISTINCT content_hash FROM files WHERE path IN ({placeholders})",
                normalized_paths,
            ).fetchall()
        return {str(row["content_hash"]) for row in rows}

    def content_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM content_objects").fetchone()
        return int(row["count"])

    def file_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM files").fetchone()
        return int(row["count"])

    def register_content_and_files(
        self,
        content_hash: str,
        content_size: int,
        chunk_count: int,
        records: Iterable[FileRecord],
    ) -> set[str]:
        records = list(records)
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO content_objects(content_hash, size, chunk_count, indexed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    size = excluded.size,
                    chunk_count = excluded.chunk_count,
                    indexed_at = excluded.indexed_at
                """,
                (content_hash, content_size, chunk_count, now),
            )
            old_hashes = self._upsert_records(connection, records, now)
            orphaned = self._delete_orphaned_content(connection, old_hashes - {content_hash})
            connection.commit()
        return orphaned

    def associate_existing_files(
        self,
        content_hash: str,
        records: Iterable[FileRecord],
    ) -> set[str]:
        records = list(records)
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM content_objects WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            if not exists:
                raise KeyError(f"Unknown content hash: {content_hash}")
            old_hashes = self._upsert_records(connection, records, now)
            orphaned = self._delete_orphaned_content(connection, old_hashes - {content_hash})
            connection.commit()
        return orphaned

    def remove_files(self, paths: Iterable[str]) -> set[str]:
        normalized_paths = list(dict.fromkeys(paths))
        if not normalized_paths:
            return set()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in normalized_paths)
            rows = connection.execute(
                f"SELECT DISTINCT content_hash FROM files WHERE path IN ({placeholders})",
                normalized_paths,
            ).fetchall()
            hashes = {str(row["content_hash"]) for row in rows}
            connection.execute(
                f"DELETE FROM files WHERE path IN ({placeholders})",
                normalized_paths,
            )
            orphaned = self._delete_orphaned_content(connection, hashes)
            connection.commit()
        return orphaned

    @staticmethod
    def _upsert_records(
        connection: sqlite3.Connection,
        records: list[FileRecord],
        now: float,
    ) -> set[str]:
        old_hashes: set[str] = set()
        for record in records:
            previous = connection.execute(
                "SELECT content_hash FROM files WHERE path = ?",
                (record.path,),
            ).fetchone()
            if previous:
                old_hashes.add(str(previous["content_hash"]))
            connection.execute(
                """
                INSERT INTO files(path, file_name, content_hash, size, mtime_ns, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    file_name = excluded.file_name,
                    content_hash = excluded.content_hash,
                    size = excluded.size,
                    mtime_ns = excluded.mtime_ns,
                    last_seen = excluded.last_seen
                """,
                (
                    record.path,
                    record.file_name,
                    record.content_hash,
                    record.size,
                    record.mtime_ns,
                    now,
                ),
            )
        return old_hashes

    @staticmethod
    def _delete_orphaned_content(
        connection: sqlite3.Connection,
        candidate_hashes: set[str],
    ) -> set[str]:
        orphaned: set[str] = set()
        for content_hash in candidate_hashes:
            reference = connection.execute(
                "SELECT 1 FROM files WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            ).fetchone()
            if reference is None:
                connection.execute(
                    "DELETE FROM content_objects WHERE content_hash = ?",
                    (content_hash,),
                )
                orphaned.add(content_hash)
        return orphaned
