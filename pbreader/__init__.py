"""
PbReader — печать PDF и достоверный предпросмотр для аппаратов PrintBox.

Библиотека сама растеризует PDF (PDFium) и сама отправляет растр на принтер
через Win32 GDI. Внешний просмотрщик не запускается вообще — значит, поверх
интерфейса киоска нечему всплыть, и параметры задания задаём мы, а не то, что
согласился принять чужой CLI.

Предпросмотр и печать считает ОДИН модуль раскладки (pbreader.layout), поэтому
картинка на экране показывает лист, а не документ: с полями принтера, с
применённым масштабом, поворотом и режимом цвета, и с честно показанной
обрезкой, если документ не помещается.

Быстрый старт::

    from pbreader import PrintJob, SessionStore, ColorMode

    store = SessionStore()
    session = store.create("scan.pdf", PrintJob(printer="Canon LBP722", copies=2))
    png = session.preview(sheet_number=1).png     # то, что выйдет из аппарата
    session.print()

Или из интерфейса — через локальный сервис: ``pbreader serve``.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .config import Config
from .devices import resolve_device
from .document import PdfDocument, PdfError, PdfPasswordRequired
from .geometry import DeviceGeometry, SheetGeometry, build_sheet
from .job import ColorMode, Duplex, Orientation, PrintJob, ScaleMode, parse_page_range
from .layout import Placement, compute_placement
from .paper import A3, A4, A5, LEGAL, LETTER, Paper, get_paper
from .preview import SheetPreview, describe_job, render_sheet_side
from .session import PreviewSession, SessionStore
from .sheets import Sheet, plan_sheets

__all__ = [
    "A3",
    "A4",
    "A5",
    "LEGAL",
    "LETTER",
    "ColorMode",
    "Config",
    "DeviceGeometry",
    "Duplex",
    "Orientation",
    "Paper",
    "PdfDocument",
    "PdfError",
    "PdfPasswordRequired",
    "Placement",
    "PreviewSession",
    "PrintJob",
    "ScaleMode",
    "SessionStore",
    "Sheet",
    "SheetGeometry",
    "SheetPreview",
    "build_sheet",
    "compute_placement",
    "describe_job",
    "get_paper",
    "parse_page_range",
    "plan_sheets",
    "render_sheet_side",
    "resolve_device",
    "__version__",
]
