"""
Геометрия листа для конкретного принтера — с честным запасным вариантом.

Предпросмотр обязан работать всегда, в том числе когда принтер выключен, занят
или дело происходит вообще не на киоске (разработка, тесты). Поэтому реальные
поля мы спрашиваем у драйвера, а когда спросить не у кого — берём типовые и
ПОМЕЧАЕМ результат как неизмеренный: интерфейс покажет это отдельной строкой,
а не соврёт молча.

Ответ драйвера запоминается на минуту. Человек у аппарата щёлкает параметры
десятками, а поля листа зависят только от принтера, формата и лотка — опрашивать
драйвер на каждое нажатие значит открывать контекст печати впустую.
"""

from __future__ import annotations

import logging
import threading
import time

from .geometry import DeviceGeometry
from .job import PrintJob
from .printers import IS_WINDOWS

logger = logging.getLogger(__name__)

#: Сколько помнить ответ драйвера, секунды. Достаточно, чтобы пережить перебор
#: параметров, и мало, чтобы смена настроек принтера подхватилась сама.
CACHE_TTL_SECONDS = 60.0

_cache: dict[tuple, tuple[float, DeviceGeometry]] = {}
_reported: set[tuple] = set()
_lock = threading.Lock()


def _cache_key(job: PrintJob) -> tuple:
    # Поля листа зависят только от этих трёх вещей.
    return (job.printer, job.paper.name, job.tray)


def _warn_once(key: tuple, printer: str, reason: str) -> None:
    """Об одной и той же беде кричим один раз.

    Аппарат работает месяцами, а предупреждение выписывается на каждый запрос
    интерфейса: без этого журнал за сутки превращается в одну повторяющуюся
    строку, в которой не видно ничего другого.
    """
    with _lock:
        first = key not in _reported
        _reported.add(key)
    message = "Не удалось снять геометрию с принтера %r (%s) — поля показаны типовыми"
    if first:
        logger.warning(message, printer, reason)
    else:
        logger.debug(message, printer, reason)


def resolve_device(job: PrintJob) -> DeviceGeometry:
    if not (IS_WINDOWS and job.printer):
        return DeviceGeometry.nominal(job.paper)

    key = _cache_key(job)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]

    try:
        from .output.gdi import probe_device

        geometry = probe_device(job.printer, job)
    except Exception as exc:
        _warn_once(key, job.printer, exc)
        return DeviceGeometry.nominal(job.paper)

    with _lock:
        _cache[key] = (now, geometry)
        _reported.discard(key)
    return geometry


def forget_cached_devices() -> None:
    """Сбрасывает запомненное — нужно тестам и смене конфигурации принтера."""
    with _lock:
        _cache.clear()
        _reported.clear()
