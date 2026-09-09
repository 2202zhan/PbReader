"""Вывод задания на устройство и слежение за ним в очереди печати."""

from __future__ import annotations

from .jobs import JobState, JobStatus, JobTracker


def print_document(*args, **kwargs):
    """Печатает документ (только Windows). Импорт ленивый: модуль тянет Win32."""
    try:
        from .gdi import print_document as _print_document
    except ImportError as exc:
        from ..printers import PrinterUnavailable

        raise PrinterUnavailable(str(exc)) from exc

    return _print_document(*args, **kwargs)


def _gdi():
    """Ленивый импорт Win32-ветки с понятной ошибкой вне Windows."""
    try:
        from . import gdi
    except ImportError as exc:
        from ..printers import PrinterUnavailable

        raise PrinterUnavailable(str(exc)) from exc
    return gdi


def diagnose(printer: str, job=None):
    """Что принтер сообщает о себе и о выводе растра (только Windows)."""
    return _gdi().diagnose(printer, job)


def print_test_page(printer: str, job=None):
    """Пробная страница мимо PDF — проверяет сам путь до бумаги (только Windows)."""
    return _gdi().print_test_page(printer, job)


def printer_state(printer: str):
    """Состояние принтера до отправки задания (только Windows)."""
    from .jobs import printer_state as _printer_state

    return _printer_state(printer)


__all__ = [
    "JobState", "JobStatus", "JobTracker",
    "diagnose", "print_document", "print_test_page", "printer_state",
]
