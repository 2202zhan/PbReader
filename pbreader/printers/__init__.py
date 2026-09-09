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

from dataclasses import dataclass, field
from typing import Any

from .capabilities import PrinterCapabilities, PrinterInfo, Tray
from .telemetry import Telemetry

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


def resolve_snmp_host(name: str, configured: str = "") -> str | None:
    """Адрес принтера для опроса по сети: из настроек либо по его порту."""
    if configured:
        return configured
    from .network import resolve_host

    try:
        return resolve_host(name)
    except Exception as exc:  # реестр может быть недоступен
        import logging

        logging.getLogger(__name__).debug("Адрес принтера %r не определён: %s", name, exc)
        return None


def read_telemetry(name: str, community: str = "public", host: str = "", timeout: float = 2.0) -> Telemetry:
    """Снимок состояния аппарата по сети. Никогда не поднимает исключений."""
    from . import telemetry as telemetry_module

    address = resolve_snmp_host(name, host)
    if not address:
        return Telemetry(host="", error="Принтер подключён не по сети — опросить его нечем")
    return telemetry_module.read(address, community=community, timeout=timeout)


@dataclass
class Preflight:
    """Можно ли принимать оплату и печатать прямо сейчас."""

    printer: str
    ready: bool = True
    #: Причины, по которым печатать нельзя.
    blocking: list[str] = field(default_factory=list)
    #: То, о чём стоит знать, но что печати не мешает.
    warnings: list[str] = field(default_factory=list)
    telemetry: Telemetry | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "printer": self.printer, "ready": self.ready,
            "blocking": self.blocking, "warnings": self.warnings,
            "telemetry": self.telemetry.to_dict() if self.telemetry else None,
        }


#: Ниже этого остатка тонера предупреждаем, но печатать не мешаем.
LOW_SUPPLY_PERCENT = 10.0


def preflight(name: str, tray: int | None = None, community: str = "public",
              host: str = "", timeout: float = 2.0) -> Preflight:
    """Проверка ДО оплаты: готов ли аппарат, есть ли бумага и тонер.

    Спросить это до того, как аппарат взял деньги, дешевле, чем объясняться
    после. Недоступность по сети НЕ считается запретом: SNMP мог быть просто
    выключен в настройках аппарата, а печатает он при этом прекрасно.
    """
    result = Preflight(printer=name)

    if IS_WINDOWS:
        try:
            from ..output import printer_state

            state = printer_state(name)
            if not state.get("ready", True):
                result.blocking.append(f"Windows сообщает: {state.get('status', 'принтер не готов')}")
        except Exception as exc:
            result.warnings.append(f"Состояние принтера в Windows не прочитано: {exc}")

    info = read_telemetry(name, community=community, host=host, timeout=timeout)
    result.telemetry = info

    if not info.reachable:
        # Не знаем — так и говорим, но дорогу не перекрываем.
        result.warnings.append(info.error or "Аппарат не отвечает по сети — состояние неизвестно")
    else:
        result.blocking.extend(info.blocking)
        result.warnings.extend(problem for problem in info.problems if problem not in info.blocking)

        if tray is not None:
            chosen = next((t for t in info.trays if t.index == tray), None)
            if chosen is not None and chosen.level_known and chosen.is_empty:
                result.blocking.append(f"В лотке «{chosen.name}» нет бумаги")
        elif info.trays and not info.has_paper:
            result.blocking.append("Во всех лотках нет бумаги")

        for supply in info.supplies:
            if supply.percent is not None and supply.percent <= LOW_SUPPLY_PERCENT:
                result.warnings.append(f"{supply.name}: осталось {supply.percent:.0f} %")

    result.ready = not result.blocking
    return result


__all__ = [
    "IS_WINDOWS",
    "LOW_SUPPLY_PERCENT",
    "Preflight",
    "Telemetry",
    "preflight",
    "read_telemetry",
    "resolve_snmp_host",
    "PrinterCapabilities",
    "PrinterInfo",
    "PrinterUnavailable",
    "Tray",
    "default_printer",
    "describe",
    "list_printers",
    "list_trays",
]
