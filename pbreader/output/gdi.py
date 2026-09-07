"""
Вывод на принтер через Win32 GDI.

Никакого внешнего процесса, никакого окна: задание — это последовательность
вызовов StartDoc/StartPage/StretchDIBits/EndPage/EndDoc на DC принтера. Именно
поэтому поверх интерфейса киоска ничего не всплывает — всплывать нечему.

Растр приходит готовым из pbreader.raster: там он уже повёрнут, отмасштабирован
и обрезан по печатаемой области ровно так, как это показал предпросмотр.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image

from ..document import PdfDocument
from ..geometry import DeviceGeometry
from ..job import ColorMode, PrintJob
from ..layout import compute_placement, resolve_orientation
from ..raster import iter_print_bands, print_dpi_for
from ..sheets import emission_order, plan_sheets
from ..units import PT_PER_INCH, Rect, Size

logger = logging.getLogger(__name__)

_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# GetDeviceCaps
HORZRES, VERTRES = 8, 10
LOGPIXELSX, LOGPIXELSY = 88, 90
PHYSICALWIDTH, PHYSICALHEIGHT = 110, 111
PHYSICALOFFSETX, PHYSICALOFFSETY = 112, 113

DIB_RGB_COLORS = 0
SRCCOPY = 0x00CC0020
COLORONCOLOR = 3
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class DOCINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_int),
        ("lpszDocName", wintypes.LPCWSTR),
        ("lpszOutput", wintypes.LPCWSTR),
        ("lpszDatatype", wintypes.LPCWSTR),
        ("fwType", wintypes.DWORD),
    ]


_gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
_gdi32.GetDeviceCaps.restype = ctypes.c_int
_gdi32.StartDocW.argtypes = [wintypes.HDC, ctypes.POINTER(DOCINFOW)]
_gdi32.StartDocW.restype = ctypes.c_int
_gdi32.StartPage.argtypes = [wintypes.HDC]
_gdi32.StartPage.restype = ctypes.c_int
_gdi32.EndPage.argtypes = [wintypes.HDC]
_gdi32.EndPage.restype = ctypes.c_int
_gdi32.EndDoc.argtypes = [wintypes.HDC]
_gdi32.EndDoc.restype = ctypes.c_int
_gdi32.AbortDoc.argtypes = [wintypes.HDC]
_gdi32.AbortDoc.restype = ctypes.c_int
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
_gdi32.SetStretchBltMode.restype = ctypes.c_int
_gdi32.StretchDIBits.argtypes = [
    wintypes.HDC,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFOHEADER),
    ctypes.c_uint,
    wintypes.DWORD,
]
_gdi32.StretchDIBits.restype = ctypes.c_int


class PrintFailed(RuntimeError):
    """Задание не удалось отправить на принтер."""


@dataclass
class PrintResult:
    printer: str
    sheets: int
    pages_printed: int
    dpi: int
    job_id: int | None = None
    warnings: list[str] = field(default_factory=list)


def device_geometry(hdc: int) -> DeviceGeometry:
    """Снимает с DC реальную геометрию листа: размер бумаги и область печати.

    Именно этих цифр не хватало предпросмотру. Принтер не печатает до края:
    у Canon LBP722 и HP M507 по краям остаётся непечатаемая рамка, и то, что в
    неё попало, теряется молча — ни один просмотрщик об этом не предупреждает.
    """
    caps = lambda index: _gdi32.GetDeviceCaps(hdc, index)  # noqa: E731
    dpi_x, dpi_y = caps(LOGPIXELSX) or 600, caps(LOGPIXELSY) or 600
    if dpi_x != dpi_y:
        logger.warning("Несимметричное разрешение принтера %dx%d dpi — считаем по меньшему", dpi_x, dpi_y)

    px_to_pt_x = lambda px: px / dpi_x * PT_PER_INCH  # noqa: E731
    px_to_pt_y = lambda px: px / dpi_y * PT_PER_INCH  # noqa: E731

    paper = Size(px_to_pt_x(caps(PHYSICALWIDTH)), px_to_pt_y(caps(PHYSICALHEIGHT)))
    printable = Rect(
        px_to_pt_x(caps(PHYSICALOFFSETX)),
        px_to_pt_y(caps(PHYSICALOFFSETY)),
        px_to_pt_x(caps(HORZRES)),
        px_to_pt_y(caps(VERTRES)),
    )
    return DeviceGeometry(
        paper_size=paper, printable=printable, dpi_x=dpi_x, dpi_y=dpi_y, is_measured=True
    )


def _blit(hdc: int, image: Image.Image, dest_px: tuple[int, int, int, int], origin: tuple[int, int]) -> None:
    """Кладёт картинку на DC принтера.

    DC устроен так, что его начало координат — угол ПЕЧАТАЕМОЙ области, а не
    бумаги, поэтому физическое смещение вычитается здесь.

    Формат — 32 бита BGRX сверху вниз (отрицательная высота в заголовке DIB).
    32 бита, а не 24: строка DIB обязана быть выровнена на 4 байта, и при 24
    битах её пришлось бы дополнять вручную для каждой ширины, не кратной
    четырём. Лишний байт на пиксель дешевле такой арифметики.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")
    left, top, width, height = dest_px
    data = image.tobytes("raw", "BGRX")

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = image.width
    header.biHeight = -image.height  # сверху вниз
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = BI_RGB
    header.biSizeImage = len(data)

    # Целевой прямоугольник задаём мы, исходный — какой отдал растеризатор:
    # StretchDIBits сам растянет на пиксель, если PDFium округлил размер полосы
    # в свою сторону. Без этого между полосами оставалась бы белая нить.
    result = _gdi32.StretchDIBits(
        hdc,
        left - origin[0], top - origin[1], width, height,
        0, 0, image.width, image.height,
        ctypes.c_char_p(data),
        ctypes.byref(header),
        DIB_RGB_COLORS,
        SRCCOPY,
    )
    if result == 0:
        raise PrintFailed(
            f"GDI не принял растр {image.width}×{image.height} (ошибка {ctypes.get_last_error()})"
        )


def print_document(
    document: PdfDocument,
    job: PrintJob,
    document_name: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> PrintResult:
    """Печатает документ. Возвращается после того, как задание ушло в спулер."""
    import win32gui
    import win32print

    from ..printers import PrinterUnavailable, describe

    printer_name = job.printer or win32print.GetDefaultPrinter()
    if not printer_name:
        raise PrinterUnavailable("Принтер не задан, и принтера по умолчанию в системе нет")

    capabilities = describe(printer_name)
    warnings: list[str] = []
    if job.color is ColorMode.COLOR and not capabilities.supports_color:
        warnings.append(f"Принтер {printer_name!r} чёрно-белый — цвет будет сведён в оттенки серого")

    from ..printers.windows import build_devmode

    devmode, driver_copies = build_devmode(printer_name, job, capabilities)
    software_copies = 1 if driver_copies >= job.copies else job.copies
    if software_copies > 1:
        warnings.append(
            f"Драйвер делает не более {capabilities.max_copies} копий — "
            f"{job.copies} копии печатаются повторной отправкой документа"
        )

    # int() обязателен: pywin32 отдаёт объект-обёртку, а ctypes принимает
    # только целое как HANDLE.
    hdc = int(win32gui.CreateDC("WINSPOOL", printer_name, devmode))
    if not hdc:
        raise PrintFailed(f"Не удалось открыть контекст печати для {printer_name!r}")

    try:
        device = device_geometry(hdc)
        dpi = print_dpi_for(job, device.dpi)
        origin = (
            int(round(device.printable.x / PT_PER_INCH * dpi)),
            int(round(device.printable.y / PT_PER_INCH * dpi)),
        )

        pages = job.resolve_pages(document.page_count)
        sheets = plan_sheets(pages, job.duplex, software_copies, job.collate)
        order = emission_order(sheets)

        _gdi32.SetStretchBltMode(hdc, COLORONCOLOR)

        docinfo = DOCINFOW()
        docinfo.cbSize = ctypes.sizeof(DOCINFOW)
        docinfo.lpszDocName = (document_name or document.path.name)[:255]
        docinfo.lpszOutput = None
        docinfo.lpszDatatype = None
        docinfo.fwType = 0

        job_id = _gdi32.StartDocW(hdc, ctypes.byref(docinfo))
        if job_id <= 0:
            raise PrintFailed(
                f"Принтер {printer_name!r} не принял задание (ошибка {ctypes.get_last_error()})"
            )

        printed = 0
        try:
            for index, page_number in enumerate(order, start=1):
                if _gdi32.StartPage(hdc) <= 0:
                    raise PrintFailed(f"Не удалось начать страницу {index}")
                if page_number is not None:
                    _emit_page(document, job, device, hdc, page_number, dpi, origin)
                    printed += 1
                if _gdi32.EndPage(hdc) <= 0:
                    raise PrintFailed(f"Не удалось завершить страницу {index}")
                if on_progress:
                    on_progress(index, len(order))
        except Exception:
            _gdi32.AbortDoc(hdc)
            raise

        if _gdi32.EndDoc(hdc) <= 0:
            raise PrintFailed("Принтер не закрыл задание")

        logger.info(
            "Задание отправлено: принтер=%s листов=%d страниц=%d dpi=%d копий(драйвер)=%d",
            printer_name, len(sheets), printed, dpi, driver_copies,
        )
        return PrintResult(
            printer=printer_name,
            sheets=len(sheets) * driver_copies,
            pages_printed=printed,
            dpi=dpi,
            job_id=job_id,
            warnings=warnings,
        )
    finally:
        _gdi32.DeleteDC(hdc)


def _emit_page(
    document: PdfDocument,
    job: PrintJob,
    device: DeviceGeometry,
    hdc: int,
    page_number: int,
    dpi: int,
    origin: tuple[int, int],
) -> None:
    from ..geometry import build_sheet

    page_size = document.page_size(page_number)
    sheet = build_sheet(device, resolve_orientation(page_size, job))
    placement = compute_placement(page_size, sheet, job)
    for band in iter_print_bands(document, page_number, placement, job, dpi):
        _blit(hdc, band.image, band.dest_px, origin)


def probe_device(printer_name: str, job: PrintJob) -> DeviceGeometry:
    """Снимает геометрию листа, ничего не печатая.

    Нужна предпросмотру: поля зависят и от принтера, и от выбранного формата и
    лотка, поэтому спрашиваем их у драйвера с тем же DEVMODE, с каким потом
    будем печатать. Иначе предпросмотр показал бы поля от другого лотка.
    """
    import win32gui

    from ..printers.windows import build_devmode

    devmode, _ = build_devmode(printer_name, job)
    hdc = int(win32gui.CreateDC("WINSPOOL", printer_name, devmode))
    if not hdc:
        raise PrintFailed(f"Не удалось открыть контекст принтера {printer_name!r}")
    try:
        return device_geometry(hdc)
    finally:
        _gdi32.DeleteDC(hdc)
