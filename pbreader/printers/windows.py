"""
Windows-специфика: опрос драйвера и сборка DEVMODE.

Два принципиальных отличия от прежней реализации.

1. DEVMODE НЕ ПИШЕТСЯ В СИСТЕМУ. Раньше параметры задания (цвет, дуплекс,
   лоток, копии) прописывались принтеру глобально через SetPrinter, а затем
   запускался внешний просмотрщик. Это меняло настройки принтера для всех и
   ломалось при двух заданиях подряд: второе успевало переписать настройки
   раньше, чем печаталось первое. Здесь DEVMODE живёт ровно столько, сколько
   живёт DC одного задания.

2. Выставляется поле `Fields`. Драйвер смотрит на битовую маску dmFields и
   игнорирует любое поле, бит которого не взведён. Присвоить devmode.Duplex и
   не тронуть Fields — это ровно тот случай, когда «драйвер игнорирует
   настройку»: он её просто не читает.
"""

from __future__ import annotations

import logging

import win32con
import win32print

from ..job import ColorMode, Duplex, PrintJob
from .capabilities import PrinterCapabilities, PrinterInfo, Tray

logger = logging.getLogger(__name__)

# Индексы DeviceCapabilities (wingdi.h). В win32con есть не все, поэтому здесь.
DC_PAPERS = 2
DC_BINS = 6
DC_DUPLEX = 7
DC_BINNAMES = 12
DC_ENUMRESOLUTIONS = 13
DC_COPIES = 18
DC_COLLATE = 22
DC_COLORDEVICE = 32

_ENUM_FLAGS = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS

_DUPLEX_TO_DM = {
    Duplex.SIMPLEX: win32con.DMDUP_SIMPLEX,
    Duplex.LONG_EDGE: win32con.DMDUP_VERTICAL,
    Duplex.SHORT_EDGE: win32con.DMDUP_HORIZONTAL,
}


def list_printers() -> list[PrinterInfo]:
    default = default_printer()
    printers: list[PrinterInfo] = []
    for entry in win32print.EnumPrinters(_ENUM_FLAGS, None, 2):
        name = entry.get("pPrinterName", "")
        printers.append(
            PrinterInfo(
                name=name,
                is_default=(name == default),
                driver=entry.get("pDriverName", "") or "",
                port=entry.get("pPortName", "") or "",
                status=_status_text(entry.get("Status", 0)),
            )
        )
    return printers


def default_printer() -> str | None:
    try:
        return win32print.GetDefaultPrinter()
    except Exception:  # драйвер по умолчанию может быть не задан вовсе
        return None


def _status_text(status: int) -> str:
    flags = {
        win32print.PRINTER_STATUS_OFFLINE: "не в сети",
        win32print.PRINTER_STATUS_PAPER_OUT: "нет бумаги",
        win32print.PRINTER_STATUS_PAPER_JAM: "замятие бумаги",
        win32print.PRINTER_STATUS_TONER_LOW: "мало тонера",
        win32print.PRINTER_STATUS_NO_TONER: "нет тонера",
        win32print.PRINTER_STATUS_DOOR_OPEN: "открыта крышка",
        win32print.PRINTER_STATUS_ERROR: "ошибка",
        win32print.PRINTER_STATUS_PAUSED: "приостановлен",
        win32print.PRINTER_STATUS_OUT_OF_MEMORY: "не хватает памяти",
    }
    active = [text for flag, text in flags.items() if status & flag]
    return ", ".join(active) if active else "готов"


def _capability(name: str, port: str, index: int, default=None):
    """Обёртка над DeviceCapabilities: драйверы отвечают не на все запросы."""
    try:
        return win32print.DeviceCapabilities(name, port, index)
    except Exception as exc:
        logger.debug("DeviceCapabilities(%s, %d) недоступно: %s", name, index, exc)
        return default


def describe(name: str) -> PrinterCapabilities:
    """Спрашивает у драйвера, что принтер умеет."""
    info = next((p for p in list_printers() if p.name == name), None)
    if info is None:
        from . import PrinterUnavailable

        known = ", ".join(p.name for p in list_printers()) or "нет ни одного"
        raise PrinterUnavailable(f"Принтер {name!r} не найден. Доступны: {known}")

    port = info.port
    bins = _capability(name, port, DC_BINS, default=()) or ()
    bin_names = _capability(name, port, DC_BINNAMES, default=()) or ()
    # Списки идут параллельно; если драйвер вернул их разной длины — доверяем
    # идентификаторам и подписываем безымянные лотки номером.
    trays = [
        Tray(id=int(bin_id), name=(bin_names[i].strip() if i < len(bin_names) else f"Лоток {bin_id}"))
        for i, bin_id in enumerate(bins)
    ]

    resolutions_raw = _capability(name, port, DC_ENUMRESOLUTIONS, default=()) or ()
    resolutions = [(int(x), int(y)) for x, y in resolutions_raw] if resolutions_raw else []

    return PrinterCapabilities(
        name=name,
        trays=trays,
        supports_duplex=bool(_capability(name, port, DC_DUPLEX, default=0)),
        supports_color=bool(_capability(name, port, DC_COLORDEVICE, default=0)),
        supports_collate=bool(_capability(name, port, DC_COLLATE, default=0)),
        max_copies=max(1, int(_capability(name, port, DC_COPIES, default=1) or 1)),
        resolutions=resolutions,
        driver=info.driver,
        port=port,
    )


def build_devmode(name: str, job: PrintJob, capabilities: PrinterCapabilities | None = None):
    """Собирает DEVMODE под задание. Ничего в системе не меняет.

    Ориентация здесь ВСЕГДА книжная, а формат — тот, что выбран в задании.
    Альбомность даёт поворот растра (см. pbreader.geometry): драйверу так нечего
    трактовать по-своему, и Canon UFR II, который флаг ориентации понимает
    по-своему, перестаёт быть особым случаем.
    """
    capabilities = capabilities or describe(name)
    handle = win32print.OpenPrinter(name)
    try:
        devmode = win32print.GetPrinter(handle, 2)["pDevMode"]
        if devmode is None:
            from . import PrinterUnavailable

            raise PrinterUnavailable(f"Драйвер принтера {name!r} не отдал DEVMODE")

        fields = int(devmode.Fields or 0)

        devmode.Orientation = win32con.DMORIENT_PORTRAIT
        fields |= win32con.DM_ORIENTATION

        devmode.PaperSize = job.paper.dmpaper
        fields |= win32con.DM_PAPERSIZE

        devmode.Color = (
            win32con.DMCOLOR_COLOR if job.color is ColorMode.COLOR else win32con.DMCOLOR_MONOCHROME
        )
        fields |= win32con.DM_COLOR

        if job.duplex.is_duplex and not capabilities.supports_duplex:
            logger.warning(
                "Принтер %r не умеет двустороннюю печать — задание печатается односторонним", name
            )
            duplex = Duplex.SIMPLEX
        else:
            duplex = job.duplex
        devmode.Duplex = _DUPLEX_TO_DM[duplex]
        fields |= win32con.DM_DUPLEX

        # Копии отдаёт драйвер, если тянет всё их количество: тогда растр
        # уходит на аппарат один раз, и печать заметно быстрее. Если не тянет —
        # печатаем циклом целиком, а не «часть драйвером, часть циклом»: делить
        # копии между двумя механизмами значит получить неверное их число при
        # любом остатке от деления.
        driver_copies = job.copies if capabilities.max_copies >= job.copies else 1
        devmode.Copies = driver_copies
        fields |= win32con.DM_COPIES

        if capabilities.supports_collate:
            devmode.Collate = win32con.DMCOLLATE_TRUE if job.collate else win32con.DMCOLLATE_FALSE
            fields |= win32con.DM_COLLATE

        if job.tray is not None:
            if capabilities.trays and capabilities.tray_by_id(job.tray) is None:
                available = ", ".join(f"{t.id} ({t.name})" for t in capabilities.trays)
                raise ValueError(
                    f"Принтер {name!r} не знает лотка с идентификатором {job.tray}. Доступны: {available}"
                )
            devmode.DefaultSource = job.tray
            fields |= win32con.DM_DEFAULTSOURCE

        devmode.Fields = fields

        # Даём драйверу привести DEVMODE в согласованный вид: он может поправить
        # несовместимые сочетания (скажем, лоток, из которого нельзя подать
        # выбранный формат) до того, как мы начнём печатать.
        try:
            win32print.DocumentProperties(
                0, handle, name, devmode, devmode, win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER
            )
        except Exception as exc:
            logger.warning("Драйвер %r не проверил DEVMODE (%s) — печатаем как есть", name, exc)

        return devmode, driver_copies
    finally:
        win32print.ClosePrinter(handle)
