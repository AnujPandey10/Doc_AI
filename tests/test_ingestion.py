from types import SimpleNamespace

from offline_rag.catalog import FileCatalog
from offline_rag.documents import ParsedContent
from offline_rag.ingestion import IngestionManager


class FakeParser:
    def __init__(self):
        self.calls = 0

    def parse(self, fingerprint):
        self.calls += 1
        return ParsedContent(fingerprint=fingerprint, documents=[], ids=[])


class FakeVectorIndex:
    def __init__(self):
        self.added_hashes = []
        self.deleted_hashes = []
        self.alias_updates = {}

    def add_content(self, parsed, batch_size, on_batch=None):
        self.added_hashes.append(parsed.fingerprint.content_hash)

    def delete_content(self, content_hash):
        self.deleted_hashes.append(content_hash)
        return 0

    def refresh_alias_metadata(self, content_hash, aliases):
        self.alias_updates[content_hash] = aliases


def no_progress(*_args):
    return None


def test_identical_files_are_parsed_and_indexed_once(tmp_path):
    first = tmp_path / "first.pdf"
    duplicate = tmp_path / "duplicate.pdf"
    first.write_bytes(b"identical bytes")
    duplicate.write_bytes(b"identical bytes")

    catalog = FileCatalog(tmp_path / "catalog.sqlite3")
    parser = FakeParser()
    vector_index = FakeVectorIndex()
    config = SimpleNamespace(parse_workers=2, embedding_batch_size=8)
    manager = IngestionManager(config, catalog, parser, vector_index)

    stats = manager.sync([str(first), str(duplicate)], [], no_progress)

    assert parser.calls == 1
    assert len(vector_index.added_hashes) == 1
    assert catalog.content_count() == 1
    assert catalog.file_count() == 2
    assert stats.duplicates == 1


def test_new_alias_of_existing_content_skips_parsing(tmp_path):
    first = tmp_path / "first.pdf"
    duplicate = tmp_path / "duplicate.pdf"
    first.write_bytes(b"identical bytes")
    duplicate.write_bytes(b"identical bytes")

    catalog = FileCatalog(tmp_path / "catalog.sqlite3")
    parser = FakeParser()
    vector_index = FakeVectorIndex()
    config = SimpleNamespace(parse_workers=2, embedding_batch_size=8)
    manager = IngestionManager(config, catalog, parser, vector_index)
    manager.sync([str(first)], [], no_progress)

    stats = manager.sync([str(duplicate)], [], no_progress)

    assert parser.calls == 1
    assert len(vector_index.added_hashes) == 1
    assert stats.duplicates == 1
    assert catalog.aliases(vector_index.added_hashes[0]) == ["duplicate.pdf", "first.pdf"]
