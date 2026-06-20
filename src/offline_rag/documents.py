from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from langchain_community.document_loaders import Docx2txtLoader, PyMuPDFLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from offline_rag.catalog import FileRecord

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


class FileChangedDuringRead(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Fingerprint:
    path: str
    file_name: str
    content_hash: str
    size: int
    mtime_ns: int

    def as_record(self) -> FileRecord:
        return FileRecord(
            path=self.path,
            file_name=self.file_name,
            content_hash=self.content_hash,
            size=self.size,
            mtime_ns=self.mtime_ns,
        )


@dataclass(slots=True)
class ParsedContent:
    fingerprint: Fingerprint
    documents: list[Document]
    ids: list[str]


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def discover_documents(directory: Path) -> list[str]:
    discovered: list[str] = []
    for root, _, file_names in os.walk(directory, followlinks=False):
        for file_name in file_names:
            path = Path(root) / file_name
            if is_supported(path) and path.is_file():
                discovered.append(str(path.resolve()))
    return sorted(discovered)


def sha256_file(path: str, block_size: int = 1024 * 1024) -> Fingerprint:
    resolved = Path(path).resolve()
    before = resolved.stat()
    digest = hashlib.sha256()
    with resolved.open("rb") as file_handle:
        while block := file_handle.read(block_size):
            digest.update(block)
    after = resolved.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise FileChangedDuringRead(f"File changed while hashing: {resolved}")
    return Fingerprint(
        path=str(resolved),
        file_name=resolved.name,
        content_hash=digest.hexdigest(),
        size=after.st_size,
        mtime_ns=after.st_mtime_ns,
    )


class DocumentParser:
    def __init__(self, chunk_size: int, chunk_overlap: int):
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    def parse(self, fingerprint: Fingerprint) -> ParsedContent:
        path = Path(fingerprint.path)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            pages = PyMuPDFLoader(str(path), mode="page").load()
        elif suffix == ".docx":
            pages = Docx2txtLoader(str(path)).load()
        else:
            raise ValueError(f"Unsupported file type: {suffix}")

        current = path.stat()
        if (
            current.st_size != fingerprint.size
            or current.st_mtime_ns != fingerprint.mtime_ns
        ):
            raise FileChangedDuringRead(f"File changed while parsing: {path}")

        normalized_pages: list[Document] = []
        for page_index, page in enumerate(pages):
            page_number = self._page_number(page.metadata, page_index, suffix)
            text = page.page_content.strip()
            if not text:
                continue
            normalized_pages.append(
                Document(
                    page_content=text,
                    metadata={
                        "source_file_name": fingerprint.file_name,
                        "source_file_names_json": json.dumps([fingerprint.file_name]),
                        "source_path": fingerprint.path,
                        "page_number": page_number,
                        "content_hash": fingerprint.content_hash,
                    },
                )
            )

        chunks = self.splitter.split_documents(normalized_pages)
        ids: list[str] = []
        for chunk_index, chunk in enumerate(chunks):
            chunk_id = f"{fingerprint.content_hash}:{chunk_index}"
            chunk.metadata["chunk_index"] = chunk_index
            chunk.metadata["chunk_id"] = chunk_id
            ids.append(chunk_id)
        return ParsedContent(fingerprint=fingerprint, documents=chunks, ids=ids)

    @staticmethod
    def _page_number(metadata: dict, page_index: int, suffix: str) -> int:
        if suffix == ".docx":
            # DOCX is flow-based and Docx2txtLoader has no stable rendered pagination.
            return 1
        raw_page = metadata.get("page", page_index)
        try:
            return int(raw_page) + 1
        except (TypeError, ValueError):
            return page_index + 1
