from __future__ import annotations

from collections.abc import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler, FileSystemMovedEvent

from offline_rag.documents import is_supported


class DocumentEventHandler(FileSystemEventHandler):
    def __init__(self, emit: Callable[[str, str], None]):
        self.emit = emit

    def on_created(self, event: FileSystemEvent) -> None:
        self._upsert(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._upsert(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and is_supported(event.src_path):
            self.emit("delete", event.src_path)

    def on_moved(self, event: FileSystemMovedEvent) -> None:
        if event.is_directory:
            return
        if is_supported(event.src_path):
            self.emit("delete", event.src_path)
        if is_supported(event.dest_path):
            self.emit("upsert", event.dest_path)

    def _upsert(self, event: FileSystemEvent) -> None:
        if not event.is_directory and is_supported(event.src_path):
            self.emit("upsert", event.src_path)

