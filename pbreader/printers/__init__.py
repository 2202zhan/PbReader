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


import logging as _logging

_logger = _logging.getLogger(__name__)
#: О чём уже сказали в журнал — чтобы не повторять на каждый запрос интерфейса.
_announced: set[tuple] = set()


@dataclass(frozen=True)
class SnmpTarget:
    """Куда идти за телеметрией и почему, если идти некуда."""

    host: str | None = None
    community: str = "public"
    port: str = ""
    #: Причина, по которой опрос невозможен. Пусто — всё в порядке.
    reason: str = ""

    @property
    def available(self) -> bool:
        return bool(self.host)

    def to_dict(self) -> dict[str, Any]:
        return {"host": self.host, "community": self.community, "port": self.port, "reason": self.reason}


def resolve_snmp(
    name: str,
    host: str = "",
    community: str = "",
    enabled: bool = True,
) -> SnmpTarget:
    """Определяет, у кого спрашивать телеметрию, и ГОВОРИТ ОБ ЭТОМ ВСЛУХ.

    Молчаливый пропуск опроса — то, из-за чего телеметрия однажды просто не
    заработала, и понять это по журналу было нельзя. Поэтому решение
    записывается в журнал: адрес найден, опрос выключен или адреса нет и почему.
    Повторно об одном и том же не пишем — интерфейс спрашивает часто.
    """
    if not enabled:
        return SnmpTarget(reason="опрос по сети выключен в настройках (snmp_enabled)")

    if host:
        target = SnmpTarget(host=host, community=community or "public", reason="")
        _announce(("configured", name, host), "Принтер %r: телеметрия с %s (адрес из настроек)", name, host)
        return target

    try:
        from .network import resolve_port

        found = resolve_port(name)
    except Exception as exc:  # реестр может быть недоступен
        return SnmpTarget(reason=f"настройки порта не прочитаны: {exc}")

    # Community из настроек важнее того, что прописано в порте Windows: его
    # задали осознанно.
    chosen_community = community or found.community or "public"

    if found.host:
        _announce(
            ("resolved", name, found.host),
            "Принтер %r: телеметрия с %s (порт %s, community %r)",
            name, found.host, found.port or "?", chosen_community,
        )
        return SnmpTarget(host=found.host, community=chosen_community, port=found.port)

    _announce(
        ("missing", name, found.reason),
        "Принтер %r: телеметрия недоступна — %s", name, found.reason,
    )
    return SnmpTarget(community=chosen_community, port=found.port, reason=found.reason)


def _announce(key: tuple, message: str, *args: Any) -> None:
    if key in _announced:
        _logger.debug(message, *args)
        return
    _announced.add(key)
    _logger.info(message, *args)


def forget_announced() -> None:
    """Сбрасывает память о сказанном — нужно тестам и смене настроек."""
    _announced.clear()


def resolve_snmp_host(name: str, configured: str = "") -> str | None:
    """Короткий ответ: адрес принтера для SNMP либо None."""
    return resolve_snmp(name, host=configured).host


def read_telemetry(
    name: str, community: str = "public", host: str = "", timeout: float = 2.0,
    enabled: bool = True,
) -> Telemetry:
    """Снимок состояния аппарата по сети. Никогда не поднимает исключений."""
    from . import telemetry as telemetry_module

    target = resolve_snmp(name, host=host, community=community, enabled=enabled)
    if not target.available:
        return Telemetry(host="", error=target.reason)
    return telemetry_module.read(target.host, community=target.community, timeout=timeout)


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
    "SnmpTarget",
    "forget_announced",
    "preflight",
    "read_telemetry",
    "resolve_snmp",
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
