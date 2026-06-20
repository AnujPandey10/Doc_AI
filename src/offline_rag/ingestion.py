from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from offline_rag.catalog import FileCatalog
from offline_rag.config import AppConfig
from offline_rag.documents import (
    DocumentParser,
    FileChangedDuringRead,
    Fingerprint,
    ParsedContent,
    is_supported,
    sha256_file,
)

if TYPE_CHECKING:
    from offline_rag.vector_index import VectorIndex


@dataclass(slots=True)
class IngestionStats:
    discovered: int = 0
    unchanged: int = 0
    duplicates: int = 0
    indexed_files: int = 0
    indexed_content_objects: int = 0
    indexed_chunks: int = 0
    deleted_files: int = 0
    deleted_chunks: int = 0
    failed: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "discovered": self.discovered,
            "unchanged": self.unchanged,
            "duplicates": self.duplicates,
            "indexed_files": self.indexed_files,
            "indexed_content_objects": self.indexed_content_objects,
            "indexed_chunks": self.indexed_chunks,
            "deleted_files": self.deleted_files,
            "deleted_chunks": self.deleted_chunks,
            "failed": self.failed,
        }


ProgressCallback = Callable[[str, int, int, str, IngestionStats], None]


class IngestionManager:
    def __init__(
        self,
        config: AppConfig,
        catalog: FileCatalog,
        parser: DocumentParser,
        vector_index: VectorIndex,
    ):
        self.config = config
        self.catalog = catalog
        self.parser = parser
        self.vector_index = vector_index

    def sync(
        self,
        candidate_paths: Iterable[str],
        deleted_paths: Iterable[str],
        progress: ProgressCallback,
        force_rehash: bool = False,
    ) -> IngestionStats:
        paths = sorted(
            {
                str(Path(path).resolve())
                for path in candidate_paths
                if is_supported(path)
            }
        )
        deleted = sorted({str(Path(path).resolve()) for path in deleted_paths})
        stats = IngestionStats(discovered=len(paths))
        metadata_refresh_hashes = self.catalog.content_hashes_for_paths(deleted)

        self._remove_deleted(deleted, stats, progress)

        changed: list[str] = []
        for path in paths:
            try:
                file_stat = os.stat(path)
            except (FileNotFoundError, PermissionError, OSError) as exc:
                stats.failed[path] = str(exc)
                continue
            existing = self.catalog.get_file(path)
            if (
                not force_rehash
                and existing
                and existing.size == file_stat.st_size
                and existing.mtime_ns == file_stat.st_mtime_ns
            ):
                stats.unchanged += 1
            else:
                changed.append(path)
                if existing:
                    metadata_refresh_hashes.add(existing.content_hash)

        progress("hashing", 0, len(changed), "Hashing new and modified files", stats)
        fingerprints = self._hash_changed(changed, stats, progress)
        grouped: dict[str, list[Fingerprint]] = defaultdict(list)
        for fingerprint in fingerprints:
            grouped[fingerprint.content_hash].append(fingerprint)

        new_groups: dict[str, list[Fingerprint]] = {}
        orphaned_hashes: set[str] = set()
        for content_hash, group in grouped.items():
            if self.catalog.has_content(content_hash):
                orphaned_hashes.update(
                    self.catalog.associate_existing_files(
                        content_hash,
                        [fingerprint.as_record() for fingerprint in group],
                    )
                )
                stats.duplicates += len(group)
                self.vector_index.refresh_alias_metadata(
                    content_hash,
                    self.catalog.aliases(content_hash),
                )
            else:
                new_groups[content_hash] = group
                stats.duplicates += max(0, len(group) - 1)

        parsed_by_hash = self._parse_new_groups(new_groups, stats, progress)
        total_content = len(parsed_by_hash)
        for content_index, (content_hash, parsed) in enumerate(parsed_by_hash.items(), start=1):
            group = new_groups[content_hash]
            progress(
                "embedding",
                content_index - 1,
                total_content,
                f"Embedding {group[0].file_name}",
                stats,
            )
            try:
                self.vector_index.add_content(
                    parsed,
                    self.config.embedding_batch_size,
                    on_batch=lambda done, total, name=group[0].file_name: progress(
                        "embedding",
                        done,
                        total,
                        f"Embedding chunks from {name}",
                        stats,
                    ),
                )
                orphaned_hashes.update(
                    self.catalog.register_content_and_files(
                        content_hash=content_hash,
                        content_size=parsed.fingerprint.size,
                        chunk_count=len(parsed.documents),
                        records=[fingerprint.as_record() for fingerprint in group],
                    )
                )
                self.vector_index.refresh_alias_metadata(
                    content_hash,
                    self.catalog.aliases(content_hash),
                )
                stats.indexed_files += len(group)
                stats.indexed_content_objects += 1
                stats.indexed_chunks += len(parsed.documents)
            except Exception as exc:
                # Deterministic IDs make retry safe; remove partial batches first.
                self.vector_index.delete_content(content_hash)
                for fingerprint in group:
                    stats.failed[fingerprint.path] = f"{type(exc).__name__}: {exc}"

        self._delete_orphaned_vectors(orphaned_hashes, stats)
        for content_hash in metadata_refresh_hashes - orphaned_hashes:
            if self.catalog.has_content(content_hash):
                self.vector_index.refresh_alias_metadata(
                    content_hash,
                    self.catalog.aliases(content_hash),
                )
        progress("finalizing", 1, 1, "Refreshing the sparse index", stats)
        return stats

    def _hash_changed(
        self,
        paths: list[str],
        stats: IngestionStats,
        progress: ProgressCallback,
    ) -> list[Fingerprint]:
        fingerprints: list[Fingerprint] = []
        with ThreadPoolExecutor(
            max_workers=self.config.parse_workers,
            thread_name_prefix="rag-hash",
        ) as executor:
            futures = {executor.submit(sha256_file, path): path for path in paths}
            for completed, future in enumerate(as_completed(futures), start=1):
                path = futures[future]
                try:
                    fingerprints.append(future.result())
                except Exception as exc:
                    stats.failed[path] = f"{type(exc).__name__}: {exc}"
                progress(
                    "hashing",
                    completed,
                    len(paths),
                    f"Hashed {completed} of {len(paths)} changed files",
                    stats,
                )
        return fingerprints

    def _parse_new_groups(
        self,
        groups: dict[str, list[Fingerprint]],
        stats: IngestionStats,
        progress: ProgressCallback,
    ) -> dict[str, ParsedContent]:
        parsed_by_hash: dict[str, ParsedContent] = {}
        progress("parsing", 0, len(groups), "Parsing and chunking unique content", stats)

        def parse_with_fallback(group: list[Fingerprint]) -> ParsedContent:
            last_error: Exception | None = None
            for fingerprint in group:
                try:
                    return self.parser.parse(fingerprint)
                except (FileNotFoundError, PermissionError, FileChangedDuringRead, OSError) as exc:
                    last_error = exc
            if last_error:
                raise last_error
            raise RuntimeError("No readable file remained in the duplicate group")

        with ThreadPoolExecutor(
            max_workers=self.config.parse_workers,
            thread_name_prefix="rag-parse",
        ) as executor:
            futures = {
                executor.submit(parse_with_fallback, group): (content_hash, group)
                for content_hash, group in groups.items()
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                content_hash, group = futures[future]
                try:
                    parsed_by_hash[content_hash] = future.result()
                except Exception as exc:
                    for fingerprint in group:
                        stats.failed[fingerprint.path] = f"{type(exc).__name__}: {exc}"
                progress(
                    "parsing",
                    completed,
                    len(groups),
                    f"Parsed {completed} of {len(groups)} unique documents",
                    stats,
                )
        return parsed_by_hash

    def _remove_deleted(
        self,
        deleted_paths: list[str],
        stats: IngestionStats,
        progress: ProgressCallback,
    ) -> None:
        if not deleted_paths:
            return
        progress("deleting", 0, len(deleted_paths), "Removing deleted documents", stats)
        orphaned = self.catalog.remove_files(deleted_paths)
        stats.deleted_files += len(deleted_paths)
        self._delete_orphaned_vectors(orphaned, stats)
        progress(
            "deleting",
            len(deleted_paths),
            len(deleted_paths),
            "Deleted document cleanup complete",
            stats,
        )

    def _delete_orphaned_vectors(
        self,
        orphaned_hashes: set[str],
        stats: IngestionStats,
    ) -> None:
        for content_hash in orphaned_hashes:
            stats.deleted_chunks += self.vector_index.delete_content(content_hash)
