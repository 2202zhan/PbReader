"""
Уборка рабочего каталога.

Каждое задание оставляет на диске файлы: скачанный оригинал, результат
конвертации из Word, загруженный через окно документ. Аппарат работает
месяцами и печатает сотни заданий в день — без уборки диск заканчивается, и
происходит это не в тестах, а через полгода на живом киоске. В прежнем
print.py уборка была написана и закомментирована.

Два правила, из-за которых это не просто «удалить старое»:

* файлы открытых сессий не трогаем — по ним прямо сейчас показывают
  предпросмотр, и в Windows такой файл всё равно заперт;
* ошибка удаления никогда не роняет уборку. Заперт файл — пропускаем и
  вернёмся через час; уборка не та задача, ради которой стоит прерывать
  печать.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

logger = logging.getLogger(__name__)


@dataclass
class SweepResult:
    removed: int = 0
    freed_bytes: int = 0
    kept_in_use: int = 0
    failed: int = 0

    @property
    def freed_mb(self) -> float:
        return self.freed_bytes / 1024 / 1024

    def __bool__(self) -> bool:
        return bool(self.removed or self.failed)


def sweep(
    work_dir: str | Path,
    max_age_hours: float = 24.0,
    keep: Iterable[str | Path] = (),
) -> SweepResult:
    """Удаляет из рабочего каталога файлы старше `max_age_hours`.

    `keep` — пути, которые нельзя трогать (открытые сессии). Возраст считаем по
    времени последнего изменения: скачали файл, напечатали, он лежит — через
    сутки не нужен.
    """
    work_dir = Path(work_dir)
    result = SweepResult()
    if not work_dir.is_dir():
        return result

    protected = {Path(path).resolve() for path in keep}
    deadline = time.time() - max_age_hours * 3600

    for entry in work_dir.iterdir():
        if not entry.is_file():
            continue
        try:
            if entry.resolve() in protected:
                result.kept_in_use += 1
                continue
            stat = entry.stat()
            if stat.st_mtime > deadline:
                continue
            size = stat.st_size
            entry.unlink()
            result.removed += 1
            result.freed_bytes += size
        except OSError as exc:
            # На Windows открытый файл удалить нельзя — это нормальный ход
            # событий, а не сбой: попробуем в следующий раз.
            result.failed += 1
            logger.debug("Не удалось удалить %s: %s", entry.name, exc)

    if result:
        logger.info(
            "Уборка %s: удалено %d файлов (%.1f МБ), занято сессиями %d, не удалось %d",
            work_dir, result.removed, result.freed_mb, result.kept_in_use, result.failed,
        )
    return result


class Housekeeper:
    """Фоновая уборка по расписанию. Останавливается вместе с сервисом."""

    def __init__(
        self,
        work_dir: str | Path,
        max_age_hours: float = 24.0,
        interval_minutes: float = 60.0,
        keep_provider: Callable[[], Iterable[str | Path]] | None = None,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.max_age_hours = max_age_hours
        self.interval_seconds = max(60.0, interval_minutes * 60.0)
        self.keep_provider = keep_provider or (lambda: ())
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sweep_now(self) -> SweepResult:
        try:
            return sweep(self.work_dir, self.max_age_hours, self.keep_provider())
        except Exception as exc:  # уборка не имеет права ронять сервис
            logger.warning("Уборка не удалась: %s", exc)
            return SweepResult()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="pbreader-housekeeping", daemon=True)
        self._thread.start()
        logger.info(
            "Уборка рабочего каталога: раз в %.0f мин, срок хранения %.0f ч",
            self.interval_seconds / 60, self.max_age_hours,
        )

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # Первый проход сразу: между запусками аппарат мог простоять неделю.
        self.sweep_now()
        while not self._stop.wait(self.interval_seconds):
            self.sweep_now()
