"""
Принтеры: что в системе есть и что оно умеет.

Список лотков, поддержку дуплекса и цвета спрашиваем у САМОГО ДРАЙВЕРА, а не
берём из настроек. Это закрывает главную дыру прежней схемы: лоток задавался
руками, строкой вроде «Tray 2», и на одном пути печати работал, а на другом
молча игнорировался — задание уходило в лоток по умолчанию, и узнать об этом
было неоткуда.
"""

from __future__ import annotations

import platform

from .capabilities import PrinterCapabilities, PrinterInfo, Tray

IS_WINDOWS = platform.system() == "Windows"


class PrinterUnavailable(RuntimeError):
    """Принтера нет в системе, либо к нему нет доступа."""


def _backend():
    if not IS_WINDOWS:
        raise PrinterUnavailable(
            f"Печать доступна только в Windows (текущая система: {platform.system()}). "
            f"Раскладку и предпросмотр можно считать где угодно."
        )
    from . import windows

    return windows


def list_printers() -> list[PrinterInfo]:
    return _backend().list_printers()


def default_printer() -> str | None:
    return _backend().default_printer()


def describe(name: str) -> PrinterCapabilities:
    return _backend().describe(name)


def list_trays(name: str) -> list[Tray]:
    return describe(name).trays


__all__ = [
    "IS_WINDOWS",
    "PrinterCapabilities",
    "PrinterInfo",
    "PrinterUnavailable",
    "Tray",
    "default_printer",
    "describe",
    "list_printers",
    "list_trays",
]
