"""
Как узнать сетевой адрес принтера, зная только его имя в Windows.

Человек, заводящий аппарат, знает принтер по названию из системы. Адрес для
SNMP спрятан в настройках порта, и спрашивать его руками — лишний повод
ошибиться и получить телеметрию не от того аппарата.

Ищем во всех мониторах портов, какие есть в системе, а не в заранее известном
списке: у HP, Canon и WSD свои мониторы, и перечислить их наперёд нельзя.
Заодно оттуда же берётся community, которое Windows уже настроила для этого
порта, — обычно оно совпадает с тем, что задано в самом аппарате.

Если адрес определить не удалось, наружу уходит ПРИЧИНА, а не просто «нет».
Молчаливый пропуск — это ровно то, из-за чего телеметрия однажды не заработала,
и понять это по журналу было невозможно.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MONITORS_KEY = r"SYSTEM\CurrentControlSet\Control\Print\Monitors"

#: Имя порта вида «IP_192.168.1.50» или «192.168.1.50_1».
_ADDRESS_IN_NAME = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")

#: Порты локального подключения — сетевого адреса у них нет по определению.
_LOCAL_PREFIXES = ("USB", "LPT", "COM", "FILE", "NUL", "PORTPROMPT", "XPSPORT", "SHRFAX")


@dataclass(frozen=True)
class PortAddress:
    """Что удалось узнать о порте принтера."""

    port: str = ""
    host: str | None = None
    #: Community, настроенное для этого порта в Windows.
    community: str | None = None
    monitor: str = ""
    #: Почему адреса нет — человеческим языком.
    reason: str = ""

    @property
    def is_local(self) -> bool:
        return bool(self.port) and self.port.upper().startswith(_LOCAL_PREFIXES)


def _looks_like_address(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        # Имя узла тоже годится: SNMP пойдёт через разрешение имён.
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{1,253}", value)) and "." in value


def _read_port_key(hive, path: str) -> dict[str, object]:
    import winreg

    values: dict[str, object] = {}
    try:
        with winreg.OpenKey(hive, path) as key:
            count = winreg.QueryInfoKey(key)[1]
            for index in range(count):
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    continue
                values[name] = value
    except OSError:
        return {}
    return values


def _search_monitors(port: str) -> tuple[dict[str, object], str]:
    """Ищет настройки порта во всех мониторах печати."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, MONITORS_KEY) as monitors:
            names = []
            for index in range(winreg.QueryInfoKey(monitors)[0]):
                try:
                    names.append(winreg.EnumKey(monitors, index))
                except OSError:
                    break
    except OSError as exc:
        logger.debug("Список мониторов печати недоступен: %s", exc)
        return {}, ""

    for monitor in names:
        values = _read_port_key(
            winreg.HKEY_LOCAL_MACHINE, f"{MONITORS_KEY}\\{monitor}\\Ports\\{port}"
        )
        if values:
            return values, monitor
    return {}, ""


def describe_port(printer_name: str, port: str) -> PortAddress:
    """Разбирает порт принтера: адрес, community и причину, если адреса нет."""
    from . import IS_WINDOWS

    port = (port or "").strip()
    if not port:
        return PortAddress(reason="у принтера не указан порт")

    result = PortAddress(port=port)
    if result.is_local:
        return PortAddress(port=port, reason="принтер подключён кабелем — по сети его не опросить")

    values, monitor = _search_monitors(port) if IS_WINDOWS else ({}, "")

    host = None
    for key in ("HostName", "IPAddress"):
        candidate = str(values.get(key, "") or "").strip()
        if _looks_like_address(candidate):
            host = candidate
            break

    community = None
    raw_community = str(values.get("SNMP Community", "") or "").strip()
    if raw_community:
        community = raw_community

    if host is None:
        # Порт часто назван самим адресом: «IP_192.168.1.50» или «192.168.1.50».
        match = _ADDRESS_IN_NAME.search(port)
        if match and _looks_like_address(match.group(1)):
            host = match.group(1)
        elif _looks_like_address(port):
            host = port

    if host:
        return PortAddress(port=port, host=host, community=community, monitor=monitor)

    if port.upper().startswith("WSD"):
        reason = (
            "порт WSD не хранит адрес принтера — укажите его в настройке snmp_host "
            "или переустановите принтер на «Стандартный порт TCP/IP»"
        )
    elif not IS_WINDOWS:
        reason = "адрес порта читается только в Windows"
    else:
        reason = (
            f"в настройках порта {port!r} адреса нет — укажите его в настройке snmp_host"
        )
    return PortAddress(port=port, community=community, monitor=monitor, reason=reason)


def resolve_port(printer_name: str) -> PortAddress:
    """То же, но порт берётся у самого принтера."""
    from . import IS_WINDOWS

    if not IS_WINDOWS:
        return PortAddress(reason="адрес порта читается только в Windows")

    from . import list_printers

    info = next((p for p in list_printers() if p.name == printer_name), None)
    if info is None:
        return PortAddress(reason=f"принтер {printer_name!r} в системе не найден")
    return describe_port(printer_name, info.port)


def resolve_host(printer_name: str, port: str | None = None) -> str | None:
    """Короткий ответ: адрес принтера либо None."""
    found = describe_port(printer_name, port) if port is not None else resolve_port(printer_name)
    return found.host
