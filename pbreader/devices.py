"""
Геометрия листа для конкретного принтера — с честным запасным вариантом.

Предпросмотр обязан работать всегда, в том числе когда принтер выключен, занят
или дело происходит вообще не на киоске (разработка, тесты). Поэтому реальные
поля мы спрашиваем у драйвера, а когда спросить не у кого — берём типовые и
ПОМЕЧАЕМ результат как неизмеренный: интерфейс покажет это отдельной строкой,
а не соврёт молча.
"""

from __future__ import annotations

import logging

from .geometry import DeviceGeometry
from .job import PrintJob
from .printers import IS_WINDOWS

logger = logging.getLogger(__name__)


def resolve_device(job: PrintJob) -> DeviceGeometry:
    if IS_WINDOWS and job.printer:
        try:
            from .output.gdi import probe_device

            return probe_device(job.printer, job)
        except Exception as exc:
            logger.warning(
                "Не удалось снять геометрию с принтера %r (%s) — поля показаны типовыми",
                job.printer, exc,
            )
    return DeviceGeometry.nominal(job.paper)
