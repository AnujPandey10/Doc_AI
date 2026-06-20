from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

from chromadb.config import Settings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from offline_rag.documents import ParsedContent


class VectorIndex:
    """Thread-safe facade over a persistent local Chroma collection."""

    def __init__(self, persist_directory: Path, embeddings: Embeddings):
        persist_directory.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self.store = Chroma(
            collection_name="documents",
            embedding_function=embeddings,
            persist_directory=str(persist_directory),
            collection_metadata={"hnsw:space": "cosine"},
            client_settings=Settings(anonymized_telemetry=False),
        )

    def add_content(
        self,
        parsed: ParsedContent,
        batch_size: int,
        on_batch: Callable[[int, int], None] | None = None,
    ) -> None:
        total = len(parsed.documents)
        with self._write_lock:
            for start in range(0, total, batch_size):
                end = min(start + batch_size, total)
                self.store.add_documents(
                    documents=parsed.documents[start:end],
                    ids=parsed.ids[start:end],
                )
                if on_batch:
                    on_batch(end, total)

    def delete_content(self, content_hash: str) -> int:
        with self._write_lock:
            payload = self.store.get(
                where={"content_hash": content_hash},
                include=["metadatas"],
            )
            ids = list(payload.get("ids", []))
            if ids:
                self.store.delete(ids=ids)
            return len(ids)

    def refresh_alias_metadata(self, content_hash: str, aliases: list[str]) -> None:
        if not aliases:
            return
        with self._write_lock:
            payload = self.store.get(
                where={"content_hash": content_hash},
                include=["metadatas"],
            )
            ids = list(payload.get("ids", []))
            metadatas = list(payload.get("metadatas", []))
            if not ids:
                return
            alias_json = json.dumps(sorted(set(aliases)))
            updated = []
            for metadata in metadatas:
                next_metadata = dict(metadata or {})
                next_metadata["source_file_name"] = aliases[0]
                next_metadata["source_file_names_json"] = alias_json
                updated.append(next_metadata)
            # Chroma's collection update changes metadata without re-embedding.
            self.store._collection.update(ids=ids, metadatas=updated)  # noqa: SLF001

    def iter_documents(self, page_size: int = 2000) -> Iterator[Document]:
        offset = 0
        while True:
            payload = self.store.get(
                limit=page_size,
                offset=offset,
                include=["documents", "metadatas"],
            )
            texts = payload.get("documents", [])
            metadatas = payload.get("metadatas", [])
            if not texts:
                break
            for text, metadata in zip(texts, metadatas, strict=False):
                if text:
                    yield Document(page_content=text, metadata=metadata or {})
            offset += len(texts)
            if len(texts) < page_size:
                break

    def count(self) -> int:
        return int(self.store._collection.count())  # noqa: SLF001
