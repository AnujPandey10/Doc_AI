from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from langchain_core.messages import BaseMessage
from watchdog.observers import Observer

from offline_rag.catalog import FileCatalog
from offline_rag.config import AppConfig
from offline_rag.documents import DocumentParser, discover_documents
from offline_rag.ingestion import IngestionManager, IngestionStats
from offline_rag.retrieval import AnswerResult, RetrievalService, build_embeddings
from offline_rag.vector_index import VectorIndex
from offline_rag.watcher import DocumentEventHandler

INDEX_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    phase: str = "idle"
    progress: float = 0.0
    completed: int = 0
    total: int = 0
    indexed_chunks: int = 0
    message: str = "No directory selected"
    error: str | None = None
    ready: bool = False
    stats: dict[str, object] = field(default_factory=dict)


class DirectoryRuntime:
    def __init__(self, config: AppConfig, directories: list[Path], index_directory: Path):
        self.config = config
        self.directories = directories
        self.index_directory = index_directory
        self._state = RuntimeSnapshot(phase="starting", message="Starting local runtime")
        self._state_lock = threading.RLock()
        self._operation_lock = threading.RLock()
        self._events: queue.Queue[tuple[str, str]] = queue.Queue()
        self._stop_event = threading.Event()
        self._observer: Observer | None = None
        self._worker: threading.Thread | None = None
        self.catalog: FileCatalog | None = None
        self.vector_index: VectorIndex | None = None
        self.ingestion: IngestionManager | None = None
        self.retrieval: RetrievalService | None = None

    def start(self) -> None:
        self._worker = threading.Thread(
            target=self._run,
            name=f"rag-runtime-{self.index_directory.name}",
            daemon=True,
        )
        self._worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._events.put(("stop", ""))
        observer = self._observer
        if observer:
            observer.stop()
            observer.join(timeout=5)
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=10)

    def snapshot(self) -> RuntimeSnapshot:
        with self._state_lock:
            return replace(self._state, stats=dict(self._state.stats))

    def enqueue(self, kind: str, path: str = "") -> None:
        self._events.put((kind, path))

    def request_full_scan(self) -> None:
        self.enqueue("full_scan", "")

    def answer(self, question: str, chat_history: list[BaseMessage]) -> AnswerResult:
        snapshot = self.snapshot()
        if not snapshot.ready:
            raise RuntimeError("The document index is not ready")
        with self._operation_lock:
            if self.retrieval is None:
                raise RuntimeError("The retrieval service is unavailable")
            return self.retrieval.answer(question, chat_history)

    def _run(self) -> None:
        try:
            self.config.apply_offline_environment()
            self._set_state(
                phase="loading_models",
                progress=0.02,
                message="Loading the local embedding model",
            )
            embeddings = build_embeddings(self.config)
            if self._stop_event.is_set():
                return
            self.catalog = FileCatalog(self.index_directory / "catalog.sqlite3")
            self.vector_index = VectorIndex(self.index_directory / "chroma", embeddings)
            parser = DocumentParser(
                chunk_size=self.config.chunk_size,
                chunk_overlap=self.config.chunk_overlap,
            )
            self.ingestion = IngestionManager(
                config=self.config,
                catalog=self.catalog,
                parser=parser,
                vector_index=self.vector_index,
            )
            self._start_observer()
            if self._stop_event.is_set():
                return
            self._perform_full_scan()
            if self._stop_event.is_set():
                return
            self._set_state(
                phase="loading_models",
                progress=0.95,
                message="Loading the local cross-encoder reranker",
            )
            self.retrieval = RetrievalService(self.config, self.vector_index)
            self._mark_ready("Index ready; filesystem monitoring is active")
            self._event_loop()
        except Exception as exc:
            self._set_state(
                phase="error",
                progress=0.0,
                message="Local runtime failed",
                error=f"{type(exc).__name__}: {exc}",
                ready=False,
            )
            if self._observer:
                self._observer.stop()
        finally:
            if self._observer:
                self._observer.stop()
                self._observer.join(timeout=5)

    def _start_observer(self) -> None:
        handler = DocumentEventHandler(self.enqueue)
        self._observer = Observer()
        for directory in self.directories:
            self._observer.schedule(handler, str(directory), recursive=True)
        self._observer.start()

    def _event_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                kind, path = self._events.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind == "stop":
                return
            try:
                if kind == "full_scan":
                    self._perform_full_scan()
                    self._refresh_retrieval()
                    self._mark_ready("Full re-scan complete; monitoring for changes")
                    continue

                pending: dict[str, str] = {str(Path(path).resolve()): kind}
                deadline = time.monotonic() + 0.75
                while time.monotonic() < deadline:
                    try:
                        next_kind, next_path = self._events.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if next_kind == "stop":
                        return
                    if next_kind == "full_scan":
                        pending.clear()
                        self._perform_full_scan()
                        self._refresh_retrieval()
                        self._mark_ready("Full re-scan complete; monitoring for changes")
                        break
                    pending[str(Path(next_path).resolve())] = next_kind
                if not pending:
                    continue
                upserts: list[str] = []
                deletes: list[str] = []
                for event_path, event_kind in pending.items():
                    if event_kind == "upsert" and Path(event_path).is_file():
                        upserts.append(event_path)
                    else:
                        deletes.append(event_path)
                self._perform_sync(upserts, deletes, force_rehash=True)
                self._refresh_retrieval()
                self._mark_ready("Changes indexed; monitoring for more")
            except Exception as exc:
                self._set_state(
                    phase="error",
                    message="A filesystem update could not be indexed; monitoring continues",
                    error=f"{type(exc).__name__}: {exc}",
                    ready=self.retrieval is not None,
                )

    def _perform_full_scan(self) -> None:
        if self.catalog is None:
            raise RuntimeError("Catalog not initialized")
        dir_names = ", ".join(d.name for d in self.directories)
        self._set_state(
            phase="scanning",
            progress=0.03,
            message=f"Scanning {dir_names}",
            ready=False,
            error=None,
        )
        discovered = []
        for directory in self.directories:
            discovered.extend(discover_documents(directory))
        current = set(discovered)
        stale = self.catalog.all_paths() - current
        self._perform_sync(discovered, stale)

    def _perform_sync(
        self,
        upserts: list[str],
        deletes: set[str] | list[str],
        force_rehash: bool = False,
    ) -> None:
        if self.ingestion is None:
            raise RuntimeError("Ingestion manager not initialized")
        with self._operation_lock:
            stats = self.ingestion.sync(
                candidate_paths=upserts,
                deleted_paths=deletes,
                progress=self._progress_callback,
                force_rehash=force_rehash,
            )
        self._set_state(
            stats=stats.as_dict(),
            indexed_chunks=self.vector_index.count() if self.vector_index else 0,
        )

    def _refresh_retrieval(self) -> None:
        if self.retrieval is not None:
            with self._operation_lock:
                self.retrieval.refresh_sparse_index()

    def _progress_callback(
        self,
        phase: str,
        completed: int,
        total: int,
        message: str,
        stats: IngestionStats,
    ) -> None:
        stage_ranges = {
            "deleting": (0.05, 0.12),
            "hashing": (0.12, 0.32),
            "parsing": (0.32, 0.58),
            "embedding": (0.58, 0.92),
            "finalizing": (0.92, 0.96),
        }
        start, end = stage_ranges.get(phase, (0.05, 0.95))
        ratio = completed / total if total else 1.0
        with self._state_lock:
            indexed_chunks = self._state.indexed_chunks
        self._set_state(
            phase=phase,
            progress=min(end, start + ((end - start) * ratio)),
            completed=completed,
            total=total,
            message=message,
            ready=False,
            error=None,
            stats=stats.as_dict(),
            indexed_chunks=indexed_chunks,
        )

    def _mark_ready(self, message: str) -> None:
        self._set_state(
            phase="ready",
            progress=1.0,
            completed=self.catalog.file_count() if self.catalog else 0,
            total=self.catalog.file_count() if self.catalog else 0,
            indexed_chunks=self.vector_index.count() if self.vector_index else 0,
            message=message,
            error=None,
            ready=True,
        )

    def _set_state(self, **changes) -> None:
        with self._state_lock:
            self._state = replace(self._state, **changes)


class RuntimeController:
    def __init__(self, config: AppConfig):
        self.config = config
        self._lock = threading.RLock()
        self.active: DirectoryRuntime | None = None
        self.active_key: str | None = None

    def activate(self, directories: list[str] | str) -> str:
        if isinstance(directories, str):
            directories = [directories]

        resolved_dirs = []
        for directory in directories:
            resolved = Path(directory).expanduser().resolve(strict=True)
            if not resolved.is_dir():
                raise NotADirectoryError(resolved)
            resolved_dirs.append(resolved)

        resolved_dirs = sorted(resolved_dirs, key=lambda p: str(p))

        identity = json.dumps(
            {
                "schema_version": INDEX_SCHEMA_VERSION,
                "directories": [str(d) for d in resolved_dirs],
                "embedding_model": self.config.embedding_model,
                "chunk_size": self.config.chunk_size,
                "chunk_overlap": self.config.chunk_overlap,
            },
            sort_keys=True,
        )
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        with self._lock:
            if self.active and self.active_key == key:
                return key
            if self.active:
                self.active.stop()
            index_directory = self.config.data_root / "indexes" / key
            self.active = DirectoryRuntime(self.config, resolved_dirs, index_directory)
            self.active_key = key
            self.active.start()
        return key

    def request_full_scan(self) -> None:
        with self._lock:
            if self.active:
                self.active.request_full_scan()

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            if self.active is None:
                return RuntimeSnapshot()
            return self.active.snapshot()

    def answer(self, question: str, chat_history: list[BaseMessage]) -> AnswerResult:
        with self._lock:
            if self.active is None:
                raise RuntimeError("No document directory is active")
            runtime = self.active
        return runtime.answer(question, chat_history)
