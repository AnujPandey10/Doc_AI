from pathlib import Path

from offline_rag.catalog import FileCatalog, FileRecord


def record(path: str, content_hash: str, size: int = 10, mtime_ns: int = 1) -> FileRecord:
    return FileRecord(
        path=path,
        file_name=Path(path).name,
        content_hash=content_hash,
        size=size,
        mtime_ns=mtime_ns,
    )


def test_duplicate_aliases_share_one_content_object(tmp_path):
    catalog = FileCatalog(tmp_path / "catalog.sqlite3")
    catalog.register_content_and_files("hash-a", 10, 3, [record("/docs/a.pdf", "hash-a")])

    orphaned = catalog.associate_existing_files(
        "hash-a",
        [record("/docs/copy.pdf", "hash-a")],
    )

    assert orphaned == set()
    assert catalog.content_count() == 1
    assert catalog.file_count() == 2
    assert catalog.aliases("hash-a") == ["a.pdf", "copy.pdf"]


def test_content_is_orphaned_only_after_last_alias_is_deleted(tmp_path):
    catalog = FileCatalog(tmp_path / "catalog.sqlite3")
    catalog.register_content_and_files(
        "hash-a",
        10,
        3,
        [
            record("/docs/a.pdf", "hash-a"),
            record("/docs/copy.pdf", "hash-a"),
        ],
    )

    assert catalog.remove_files(["/docs/a.pdf"]) == set()
    assert catalog.has_content("hash-a")
    assert catalog.remove_files(["/docs/copy.pdf"]) == {"hash-a"}
    assert not catalog.has_content("hash-a")


def test_modifying_a_file_orphans_its_previous_unique_content(tmp_path):
    catalog = FileCatalog(tmp_path / "catalog.sqlite3")
    catalog.register_content_and_files("old-hash", 10, 3, [record("/docs/a.pdf", "old-hash")])

    orphaned = catalog.register_content_and_files(
        "new-hash",
        11,
        4,
        [record("/docs/a.pdf", "new-hash", size=11, mtime_ns=2)],
    )

    assert orphaned == {"old-hash"}
    assert not catalog.has_content("old-hash")
    assert catalog.has_content("new-hash")


def test_hashes_can_be_looked_up_before_alias_metadata_is_refreshed(tmp_path):
    catalog = FileCatalog(tmp_path / "catalog.sqlite3")
    catalog.register_content_and_files("hash-a", 10, 3, [record("/docs/a.pdf", "hash-a")])

    assert catalog.content_hashes_for_paths(["/docs/a.pdf", "/docs/missing.pdf"]) == {
        "hash-a"
    }
