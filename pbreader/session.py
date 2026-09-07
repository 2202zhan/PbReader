"""
Сессия предпросмотра: открытый документ + параметры + выданные картинки.

Существует, чтобы интерфейс не пересылал файл и весь набор параметров ради
каждого листа. Задание создаётся один раз, дальше листы тянутся по номеру —
а параметры (масштаб, цвет, лоток, дуплекс) можно менять на лету: сессия
пересчитает раскладку и отдаст новые картинки.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .devices import resolve_device
from .document import PdfDocument
from .geometry import DeviceGeometry
from .job import PrintJob
from .preview import DEFAULT_PREVIEW_WIDTH, SheetPreview, describe_job, render_sheet_side
from .sheets import Sheet, plan_sheets

logger = logging.getLogger(__name__)


class SessionExpired(KeyError):
    pass


@dataclass
class PreviewSession:
    id: str
    document: PdfDocument
    job: PrintJob
    device: DeviceGeometry
    created_at: float
    touched_at: float

    def touch(self) -> None:
        self.touched_at = time.time()

    def update_job(self, job: PrintJob) -> None:
        """Меняет параметры задания. Геометрию листа пересчитываем: она зависит
        от принтера, формата и лотка, а их пользователь как раз и переключает."""
        self.job = job
        self.device = resolve_device(job)
        self.touch()

    @property
    def sheets(self) -> list[Sheet]:
        pages = self.job.resolve_pages(self.document.page_count)
        return plan_sheets(pages, self.job.duplex, self.job.copies, self.job.collate)

    def describe(self) -> dict[str, Any]:
        data = describe_job(self.document, self.job, self.device)
        data["session_id"] = self.id
        return data

    def preview(
        self, sheet_number: int, side: str = "front", width: int = DEFAULT_PREVIEW_WIDTH
    ) -> SheetPreview:
        sheets = self.sheets
        if not 1 <= sheet_number <= len(sheets):
            raise IndexError(f"Листа {sheet_number} нет: в задании {len(sheets)} листов")
        self.touch()
        return render_sheet_side(
            self.document, self.job, self.device, sheets[sheet_number - 1], side, width
        )

    def print(self, document_name: str | None = None):
        from .output import print_document

        self.touch()
        return print_document(self.document, self.job, document_name)

    def close(self) -> None:
        self.document.close()


class SessionStore:
    """Хранилище сессий с ограничением по времени и количеству.

    Каждая сессия держит открытый PDF, то есть память и файловый дескриптор.
    Киоск работает сутками, и без уборки старые задания просто накапливаются.
    """

    def __init__(self, ttl_seconds: int = 1800, max_sessions: int = 32) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._sessions: dict[str, PreviewSession] = {}
        self._lock = threading.RLock()

    def create(self, path: str | Path, job: PrintJob, password: str | None = None) -> PreviewSession:
        document = PdfDocument(path, password=password)
        now = time.time()
        session = PreviewSession(
            id=uuid.uuid4().hex,
            document=document,
            job=job,
            device=resolve_device(job),
            created_at=now,
            touched_at=now,
        )
        with self._lock:
            self._evict_locked()
            if len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions.values(), key=lambda s: s.touched_at)
                self._drop_locked(oldest.id)
            self._sessions[session.id] = session
        logger.info("Создана сессия %s: %s (%d стр.)", session.id, document.path.name, document.page_count)
        return session

    def get(self, session_id: str) -> PreviewSession:
        with self._lock:
            self._evict_locked()
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionExpired(f"Задание {session_id} не найдено или устарело")
            session.touch()
            return session

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._drop_locked(session_id)

    def close_all(self) -> None:
        with self._lock:
            for session_id in list(self._sessions):
                self._drop_locked(session_id)

    def open_paths(self) -> list[Path]:
        """Файлы, открытые прямо сейчас, — их уборка трогать не должна."""
        with self._lock:
            return [session.document.path for session in self._sessions.values()]

    def _drop_locked(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            session.close()
            logger.info("Сессия %s закрыта", session_id)

    def _evict_locked(self) -> None:
        deadline = time.time() - self.ttl_seconds
        for session_id in [s.id for s in self._sessions.values() if s.touched_at < deadline]:
            self._drop_locked(session_id)
