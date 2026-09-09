"""
Как узнать сетевой адрес принтера, зная только его имя в Windows.

Человек, заводящий аппарат, знает принтер по названию из системы. Адрес для
SNMP спрятан в настройках порта, и спрашивать его руками — лишний повод
ошибиться и получить телеметрию не от того аппарата.

Порядок поиска: сначала настройки порта в реестре (там адрес лежит в явном
виде), затем само имя порта — «Стандартный порт TCP/IP» его обычно называет
`IP_192.168.1.50`, а часть драйверов просто адресом.
"""

from __future__ import annotations

import ipaddress
import logging
import re

logger = logging.getLogger(__name__)

_PORT_KEYS = (
    r"SYSTEM\CurrentControlSet\Control\Print\Monitors\Standard TCP/IP Port\Ports",
    r"SYSTEM\CurrentControlSet\Control\Print\Monitors\HP Standard TCP/IP Port\Ports",
    r"SYSTEM\CurrentControlSet\Control\Print\Monitors\WSD Port\Ports",
)

#: Имя порта вида «IP_192.168.1.50» или «192.168.1.50_1».
_ADDRESS_IN_NAME = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


def _looks_like_address(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        # Имя узла тоже годится: SNMP пойдёт через разрешение имён.
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{1,253}", value)) and "." in value


def _from_registry(port: str) -> str | None:
    import winreg

    for base in _PORT_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{base}\\{port}") as key:
                for value_name in ("HostName", "IPAddress"):
                    try:
                        value, _ = winreg.QueryValueEx(key, value_name)
                    except OSError:
                        continue
                    if value and _looks_like_address(str(value)):
                        return str(value).strip()
        except OSError:
            continue
    return None


def _from_port_name(port: str) -> str | None:
    match = _ADDRESS_IN_NAME.search(port or "")
    if match and _looks_like_address(match.group(1)):
        return match.group(1)
    # Порт мог быть назван именем узла целиком.
    candidate = (port or "").strip()
    return candidate if _looks_like_address(candidate) else None


def resolve_host(printer_name: str, port: str | None = None) -> str | None:
    """Возвращает адрес принтера для SNMP, либо None для локального подключения."""
    from . import IS_WINDOWS

    if port is None:
        if not IS_WINDOWS:
            return None
        from . import list_printers

        info = next((p for p in list_printers() if p.name == printer_name), None)
        port = info.port if info else ""

    port = (port or "").strip()
    if not port or port.upper().startswith(("USB", "LPT", "COM", "FILE", "NUL", "PORTPROMPT")):
        # Локальное подключение — SNMP тут неоткуда взяться.
        return None

    address = None
    if IS_WINDOWS:
        address = _from_registry(port)
    address = address or _from_port_name(port)

    if address:
        logger.debug("Принтер %r: адрес для SNMP — %s (порт %s)", printer_name, address, port)
    else:
        logger.debug("Принтер %r: адрес по порту %r не определён", printer_name, port)
    return address
