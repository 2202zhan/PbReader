"""
Заглушки pywin32 — чтобы windows.py можно было проверять не в Windows.

Именно из-за отсутствия таких проверок разбор ответов драйвера доехал до
живого аппарата с ошибкой: DC_ENUMRESOLUTIONS отдаёт список СЛОВАРЕЙ, а код
распаковывал его как пары чисел. Здесь воспроизводится форма ответов настоящей
обвязки, чтобы то же самое не повторилось молча.
"""

from __future__ import annotations

import sys
import types
from typing import Any

# Индексы DeviceCapabilities — те же, что в pbreader/printers/windows.py.
DC_PAPERS, DC_BINS, DC_DUPLEX = 2, 6, 7
DC_BINNAMES, DC_ENUMRESOLUTIONS = 12, 13
DC_COPIES, DC_COLLATE, DC_COLORDEVICE = 18, 22, 32

_WIN32CON = {
    "DMDUP_SIMPLEX": 1, "DMDUP_VERTICAL": 2, "DMDUP_HORIZONTAL": 3,
    "DMORIENT_PORTRAIT": 1, "DMORIENT_LANDSCAPE": 2,
    "DMCOLOR_MONOCHROME": 1, "DMCOLOR_COLOR": 2,
    "DMCOLLATE_FALSE": 0, "DMCOLLATE_TRUE": 1,
    "DMPAPER_A4": 9,
    "DM_ORIENTATION": 0x0001, "DM_PAPERSIZE": 0x0002, "DM_COPIES": 0x0100,
    "DM_DEFAULTSOURCE": 0x0200, "DM_PRINTQUALITY": 0x0400, "DM_COLOR": 0x0800,
    "DM_DUPLEX": 0x1000, "DM_COLLATE": 0x8000,
    "DM_IN_BUFFER": 8, "DM_OUT_BUFFER": 2,
}

_PRINTER_STATUS = {
    "PRINTER_STATUS_PAUSED": 0x01, "PRINTER_STATUS_ERROR": 0x02,
    "PRINTER_STATUS_PAPER_JAM": 0x08, "PRINTER_STATUS_PAPER_OUT": 0x10,
    "PRINTER_STATUS_OFFLINE": 0x80, "PRINTER_STATUS_TONER_LOW": 0x00020000,
    "PRINTER_STATUS_NO_TONER": 0x00040000, "PRINTER_STATUS_DOOR_OPEN": 0x00400000,
    "PRINTER_STATUS_OUT_OF_MEMORY": 0x00200000,
}


class FakeDevMode:
    """PyDEVMODEW: набор полей, которые драйвер читает по маске Fields."""

    def __init__(self) -> None:
        self.Fields = 0
        self.Orientation = 1
        self.PaperSize = 0
        self.Color = 1
        self.Duplex = 1
        self.Copies = 1
        self.Collate = 0
        self.DefaultSource = 0


class FakeSpooler:
    """Один принтер с настраиваемыми ответами DeviceCapabilities."""

    def __init__(
        self,
        name: str = "HP LaserJet M507",
        capabilities: dict[int, Any] | None = None,
        driver: str = "HP LaserJet M507 PCL-6",
        port: str = "USB001",
        status: int = 0,
    ) -> None:
        self.name = name
        self.driver = driver
        self.port = port
        self.status = status
        self.capabilities = capabilities if capabilities is not None else self.realistic()
        self.devmode = FakeDevMode()
        self.document_properties_called = False

    @staticmethod
    def realistic() -> dict[int, Any]:
        """Ответы в той форме, в какой их отдаёт настоящий pywin32.

        Разрешения — список словарей: та самая форма, на которой всё сломалось.
        """
        return {
            DC_BINS: (1, 257, 258),
            DC_BINNAMES: ("Автовыбор", "Кассета 1", "Кассета 2"),
            DC_ENUMRESOLUTIONS: [{"xdpi": 600, "ydpi": 600}, {"xdpi": 1200, "ydpi": 1200}],
            DC_DUPLEX: 1,
            DC_COLORDEVICE: 0,
            DC_COLLATE: 1,
            DC_COPIES: 999,
        }

    # --- то, что вызывает windows.py -------------------------------------

    def EnumPrinters(self, flags, server, level):
        return [{
            "pPrinterName": self.name, "pDriverName": self.driver,
            "pPortName": self.port, "Status": self.status,
        }]

    def GetDefaultPrinter(self):
        return self.name

    def DeviceCapabilities(self, device, port, index, DevMode=None):
        if index not in self.capabilities:
            raise RuntimeError(f"драйвер не отвечает на запрос {index}")
        value = self.capabilities[index]
        if isinstance(value, Exception):
            raise value
        return value

    def OpenPrinter(self, name, defaults=None):
        return 42

    def GetPrinter(self, handle, level):
        return {
            "pDevMode": self.devmode, "pDriverName": self.driver,
            "pPortName": self.port, "Status": self.status, "cJobs": 0,
        }

    def ClosePrinter(self, handle):
        return None

    def DocumentProperties(self, hwnd, handle, name, out_devmode, in_devmode, mode):
        self.document_properties_called = True
        return 1


def install(monkeypatch, spooler: FakeSpooler) -> FakeSpooler:
    """Подменяет win32print и win32con на заглушки и перезагружает windows.py."""
    import importlib

    win32print = types.ModuleType("win32print")
    for key, value in _PRINTER_STATUS.items():
        setattr(win32print, key, value)
    win32print.PRINTER_ENUM_LOCAL = 0x02
    win32print.PRINTER_ENUM_CONNECTIONS = 0x04
    for method in ("EnumPrinters", "GetDefaultPrinter", "DeviceCapabilities",
                   "OpenPrinter", "GetPrinter", "ClosePrinter", "DocumentProperties"):
        setattr(win32print, method, getattr(spooler, method))

    win32con = types.ModuleType("win32con")
    for key, value in _WIN32CON.items():
        setattr(win32con, key, value)

    monkeypatch.setitem(sys.modules, "win32print", win32print)
    monkeypatch.setitem(sys.modules, "win32con", win32con)

    module = importlib.import_module("pbreader.printers.windows")
    monkeypatch.setattr(module, "win32print", win32print, raising=False)
    monkeypatch.setattr(module, "win32con", win32con, raising=False)
    importlib.reload(module)
    return module
