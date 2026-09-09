"""
Что аппарат сообщает о себе по сети (Printer-MIB, RFC 3805).

Здесь берётся то, чего Windows не знает в принципе. Спулер видит очередь: он
скажет, когда задание из неё ушло. Он НЕ знает, вышла ли бумага, сколько её
осталось в лотке и сколько тонера в картридже — это знает только сам аппарат.

Главное значение — `prtMarkerLifeCount`: сколько листов механизм отпечатал за
свою жизнь. Прочитать его до задания и после — единственный способ узнать
физическое число отпечатанных листов. Никакой счётчик Windows этого не даёт:
`PagesPrinted` у спулера считает страницы, ОТДАННЫЕ ДРАЙВЕРУ, а не легшие на
бумагу.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..snmp import SnmpClient, SnmpError, as_text

logger = logging.getLogger(__name__)

# Общие сведения
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_PRINTER_NAME = "1.3.6.1.2.1.43.5.1.1.16.1"
OID_SERIAL = "1.3.6.1.2.1.43.5.1.1.17.1"

#: Счётчик механизма — то самое «сколько листов реально вышло».
OID_MARKER_LIFE_COUNT = "1.3.6.1.2.1.43.10.2.1.4.1.1"

OID_DEVICE_STATUS = "1.3.6.1.2.1.25.3.2.1.5.1"
OID_PRINTER_STATUS = "1.3.6.1.2.1.25.3.5.1.1.1"
OID_ERROR_STATE = "1.3.6.1.2.1.25.3.5.1.2.1"

# Таблица лотков подачи
OID_INPUT_MAX = "1.3.6.1.2.1.43.8.2.1.9"
OID_INPUT_LEVEL = "1.3.6.1.2.1.43.8.2.1.10"
OID_INPUT_NAME = "1.3.6.1.2.1.43.8.2.1.13"
OID_INPUT_DESCRIPTION = "1.3.6.1.2.1.43.8.2.1.18"

# Таблица расходников
OID_SUPPLY_DESCRIPTION = "1.3.6.1.2.1.43.11.1.1.6"
OID_SUPPLY_MAX = "1.3.6.1.2.1.43.11.1.1.8"
OID_SUPPLY_LEVEL = "1.3.6.1.2.1.43.11.1.1.9"

#: Особые значения уровня по RFC 3805. Отрицательные — это НЕ количество.
LEVEL_OTHER, LEVEL_UNKNOWN, LEVEL_SOME_REMAINING = -1, -2, -3

_DEVICE_STATUS = {1: "неизвестно", 2: "работает", 3: "предупреждение", 4: "проверка", 5: "не работает"}
_PRINTER_STATUS = {1: "другое", 2: "неизвестно", 3: "готов", 4: "печатает", 5: "прогрев"}

#: Битовая маска hrPrinterDetectedErrorState (RFC 1759). Первый байт — старший.
_ERROR_BITS = (
    (0, 0x80, "мало бумаги", False),
    (0, 0x40, "нет бумаги", True),
    (0, 0x20, "мало тонера", False),
    (0, 0x10, "нет тонера", True),
    (0, 0x08, "открыта крышка", True),
    (0, 0x04, "замятие бумаги", True),
    (0, 0x02, "не в сети", True),
    (0, 0x01, "требуется обслуживание", True),
    (1, 0x80, "нет лотка подачи", True),
    (1, 0x40, "нет приёмного лотка", True),
    (1, 0x20, "нет картриджа", True),
    (1, 0x10, "приёмный лоток почти полон", False),
    (1, 0x08, "приёмный лоток полон", True),
    (1, 0x04, "лоток подачи пуст", True),
    (1, 0x02, "просрочено обслуживание", False),
)


def _percent(level: Any, capacity: Any) -> float | None:
    """Доля заполнения, 0..100. None — аппарат точного числа не знает."""
    if not isinstance(level, int) or not isinstance(capacity, int):
        return None
    if level < 0 or capacity <= 0:
        return None
    return round(min(100.0, level / capacity * 100), 1)


@dataclass(frozen=True)
class InputTray:
    index: int
    name: str
    level: int
    capacity: int
    percent: float | None

    @property
    def is_empty(self) -> bool:
        """Пуст ли лоток. Неизвестный уровень пустым НЕ считаем.

        Иначе принтер, не умеющий считать листы (а таких много), выглядел бы
        вечно пустым и блокировал бы печать.
        """
        return self.level == 0

    @property
    def level_known(self) -> bool:
        return self.level >= 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "name": self.name, "level": self.level,
            "capacity": self.capacity, "percent": self.percent,
            "is_empty": self.is_empty, "level_known": self.level_known,
        }


@dataclass(frozen=True)
class Supply:
    index: int
    name: str
    level: int
    capacity: int
    percent: float | None

    @property
    def is_empty(self) -> bool:
        return self.level == 0

    @property
    def level_known(self) -> bool:
        return self.level >= 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "name": self.name, "level": self.level,
            "capacity": self.capacity, "percent": self.percent,
            "is_empty": self.is_empty, "level_known": self.level_known,
        }


@dataclass
class Telemetry:
    """Снимок состояния аппарата."""

    host: str
    reachable: bool = False
    model: str = ""
    serial: str = ""
    #: Счётчик механизма: сколько листов отпечатано за всю жизнь аппарата.
    page_count: int | None = None
    device_status: str = ""
    printer_status: str = ""
    #: Неполадки словами.
    problems: list[str] = field(default_factory=list)
    #: Неполадки, при которых печатать нельзя.
    blocking: list[str] = field(default_factory=list)
    trays: list[InputTray] = field(default_factory=list)
    supplies: list[Supply] = field(default_factory=list)
    error: str = ""

    @property
    def ready(self) -> bool:
        """Можно ли печатать. Недоступный по сети аппарат готовым не считаем...

        ...но и печатать не запрещаем: SNMP мог быть просто закрыт в настройках
        аппарата, а печать при этом работает. Решение принимает вызывающий, см.
        `preflight` в pbreader.printers.
        """
        return self.reachable and not self.blocking

    @property
    def has_paper(self) -> bool:
        """Есть ли бумага хоть где-то. Лотки с неизвестным уровнем считаем полными."""
        if not self.trays:
            return True
        return any(not tray.level_known or tray.level > 0 for tray in self.trays)

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host, "reachable": self.reachable, "model": self.model,
            "serial": self.serial, "page_count": self.page_count,
            "device_status": self.device_status, "printer_status": self.printer_status,
            "problems": self.problems, "blocking": self.blocking, "ready": self.ready,
            "has_paper": self.has_paper,
            "trays": [t.to_dict() for t in self.trays],
            "supplies": [s.to_dict() for s in self.supplies],
            "error": self.error,
        }


def _decode_errors(raw: Any) -> tuple[list[str], list[str]]:
    """Разбирает битовую маску неполадок в понятные строки.

    Значение приходит именно двоичным: это набор битов, а не текст.
    """
    if isinstance(raw, str):
        raw = raw.encode("latin-1", "ignore")
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        return [], []

    problems, blocking = [], []
    for byte_index, mask, text, is_blocking in _ERROR_BITS:
        if byte_index < len(raw) and raw[byte_index] & mask:
            problems.append(text)
            if is_blocking:
                blocking.append(text)
    return problems, blocking


def _table(client: SnmpClient, base_oid: str) -> dict[int, Any]:
    """Читает столбец таблицы: {номер строки: значение}."""
    rows: dict[int, Any] = {}
    for oid, value in client.walk(base_oid):
        try:
            rows[int(oid.rsplit(".", 1)[1])] = value
        except (IndexError, ValueError):
            continue
    return rows


def read(host: str, community: str = "public", timeout: float = 2.0) -> Telemetry:
    """Снимает всё, что аппарат готов рассказать. Не поднимает исключений."""
    telemetry = Telemetry(host=host)
    client = SnmpClient(host, community=community, timeout=timeout)

    try:
        general = client.get(OID_SYS_DESCR, OID_PRINTER_NAME, OID_SERIAL,
                             OID_MARKER_LIFE_COUNT, OID_DEVICE_STATUS, OID_PRINTER_STATUS)
    except SnmpError as exc:
        telemetry.error = str(exc)
        logger.info("Принтер %s недоступен по SNMP: %s", host, exc)
        return telemetry

    telemetry.reachable = True
    telemetry.model = as_text(general.get(OID_PRINTER_NAME)) or as_text(general.get(OID_SYS_DESCR))
    telemetry.serial = as_text(general.get(OID_SERIAL))
    count = general.get(OID_MARKER_LIFE_COUNT)
    telemetry.page_count = int(count) if isinstance(count, int) else None
    telemetry.device_status = _DEVICE_STATUS.get(general.get(OID_DEVICE_STATUS), "")
    telemetry.printer_status = _PRINTER_STATUS.get(general.get(OID_PRINTER_STATUS), "")

    telemetry.problems, telemetry.blocking = _decode_errors(client.get_one(OID_ERROR_STATE))

    try:
        levels = _table(client, OID_INPUT_LEVEL)
        capacities = _table(client, OID_INPUT_MAX)
        names = _table(client, OID_INPUT_NAME)
        descriptions = _table(client, OID_INPUT_DESCRIPTION)
        telemetry.trays = [
            InputTray(
                index=index,
                name=as_text(names.get(index)) or as_text(descriptions.get(index)) or f"Лоток {index}",
                level=level if isinstance(level, int) else LEVEL_UNKNOWN,
                capacity=capacities.get(index, LEVEL_UNKNOWN),
                percent=_percent(level, capacities.get(index)),
            )
            for index, level in sorted(levels.items())
        ]
    except SnmpError as exc:
        logger.debug("Принтер %s: таблица лотков не прочитана (%s)", host, exc)

    try:
        levels = _table(client, OID_SUPPLY_LEVEL)
        capacities = _table(client, OID_SUPPLY_MAX)
        names = _table(client, OID_SUPPLY_DESCRIPTION)
        telemetry.supplies = [
            Supply(
                index=index,
                name=as_text(names.get(index)) or f"Расходник {index}",
                level=level if isinstance(level, int) else LEVEL_UNKNOWN,
                capacity=capacities.get(index, LEVEL_UNKNOWN),
                percent=_percent(level, capacities.get(index)),
            )
            for index, level in sorted(levels.items())
        ]
    except SnmpError as exc:
        logger.debug("Принтер %s: таблица расходников не прочитана (%s)", host, exc)

    return telemetry


def page_count(host: str, community: str = "public", timeout: float = 2.0) -> int | None:
    """Только счётчик механизма — быстрый запрос до и после задания."""
    try:
        value = SnmpClient(host, community=community, timeout=timeout).get_one(OID_MARKER_LIFE_COUNT)
    except SnmpError:
        return None
    return int(value) if isinstance(value, int) else None
