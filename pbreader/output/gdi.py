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
import sys
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
from .dib import pack

logger = logging.getLogger(__name__)

if sys.platform != "win32":
    # Понятная ошибка вместо «module 'ctypes' has no attribute 'WinDLL'»:
    # модуль импортируют лениво, и такое сообщение всплывало бы посреди
    # печати, ничего не объясняя тому, кто просто запустил не на том
    # компьютере.
    raise ImportError(
        f"Вывод на принтер доступен только в Windows (система: {sys.platform}). "
        f"Раскладку и предпросмотр можно считать где угодно."
    )

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
GDI_ERROR = 0xFFFFFFFF

# GetDeviceCaps: что устройство умеет с растром
TECHNOLOGY = 2
RASTERCAPS = 38
RC_BITBLT = 0x0001
RC_DI_BITMAP = 0x0080
RC_DIBTODEV = 0x0200
RC_STRETCHBLT = 0x0800
RC_STRETCHDIB = 0x2000
_RASTER_FLAGS = (
    (RC_BITBLT, "BitBlt"),
    (RC_DI_BITMAP, "DIB"),
    (RC_DIBTODEV, "SetDIBitsToDevice"),
    (RC_STRETCHBLT, "StretchBlt"),
    (RC_STRETCHDIB, "StretchDIBits"),
)


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
_gdi32.SetDIBitsToDevice.argtypes = [
    wintypes.HDC,
    ctypes.c_int, ctypes.c_int, wintypes.DWORD, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFOHEADER),
    ctypes.c_uint,
]
_gdi32.SetDIBitsToDevice.restype = ctypes.c_int


class PrintFailed(RuntimeError):
    """Задание не удалось отправить на принтер."""


@dataclass
class PrintResult:
    printer: str
    sheets: int
    #: Страниц с содержимым (пустые обороты дуплекса сюда не входят).
    pages_printed: int
    dpi: int
    #: Всего страниц отдано спулеру, включая пустые обороты. Именно это число
    #: спулер считает «TotalPages», по нему сверяется ход печати.
    pages_sent: int = 0
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


def raster_support(hdc: int) -> dict[str, bool]:
    """Что драйвер умеет делать с растром — по его собственным словам."""
    caps = _gdi32.GetDeviceCaps(hdc, RASTERCAPS)
    return {name: bool(caps & flag) for flag, name in _RASTER_FLAGS}


def _header(width: int, height: int, size: int) -> BITMAPINFOHEADER:
    """Заголовок DIB. Положительная высота = строки снизу вверх (см. dib.py)."""
    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = height  # положительная = строки снизу вверх
    header.biPlanes = 1
    header.biBitCount = 24
    header.biCompression = BI_RGB
    header.biSizeImage = size
    return header


def _blit(
    hdc: int,
    image: Image.Image,
    dest_px: tuple[int, int, int, int],
    origin: tuple[int, int],
    support: dict[str, bool] | None = None,
) -> None:
    """Кладёт картинку на DC принтера.

    DC устроен так, что его начало координат — угол ПЕЧАТАЕМОЙ области, а не
    бумаги, поэтому физическое смещение вычитается здесь.
    """
    left, top, width, height = dest_px
    dib = pack(image)
    source_width, source_height = dib.source_width, dib.source_height
    header = _header(dib.width, dib.height, len(dib.bits))
    bits = ctypes.c_char_p(dib.bits)
    x, y = left - origin[0], top - origin[1]

    support = support or {}
    attempts = []
    if support.get("StretchDIBits", True):
        attempts.append("StretchDIBits")
    if support.get("SetDIBitsToDevice", True):
        attempts.append("SetDIBitsToDevice")
    if not attempts:
        attempts = ["StretchDIBits", "SetDIBitsToDevice"]

    errors = []
    for method in attempts:
        ctypes.set_last_error(0)
        if method == "StretchDIBits":
            # Целевой прямоугольник задаём мы, исходный — какой отдал
            # растеризатор: разницу в пиксель, если PDFium округлил размер
            # полосы в свою сторону, поглощает масштабирование. Иначе между
            # полосами осталась бы белая нить.
            result = _gdi32.StretchDIBits(
                hdc, x, y, width, height,
                0, 0, source_width, source_height,
                bits, ctypes.byref(header), DIB_RGB_COLORS, SRCCOPY,
            )
        else:
            # Растр уже построен в разрешении принтера, поэтому масштабировать
            # нечего — вывод один к одному годится как запасной путь.
            result = _gdi32.SetDIBitsToDevice(
                hdc, x, y, source_width, source_height,
                0, 0, 0, dib.height,
                bits, ctypes.byref(header), DIB_RGB_COLORS,
            )
        if result not in (0, GDI_ERROR):
            return
        errors.append(f"{method}: код {result}, ошибка {ctypes.get_last_error()}")
        logger.warning("Драйвер не принял растр через %s — пробуем иначе", method)

    raise PrintFailed(
        f"Драйвер не принял растр {source_width}x{source_height}. " + "; ".join(errors)
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

        support = raster_support(hdc)
        logger.info(
            "Принтер %s: технология=%d, растр — %s",
            printer_name, _gdi32.GetDeviceCaps(hdc, TECHNOLOGY),
            ", ".join(f"{k}: {'да' if v else 'нет'}" for k, v in support.items()),
        )
        if not (support["StretchDIBits"] or support["SetDIBitsToDevice"]):
            warnings.append(
                f"Драйвер {printer_name!r} не заявляет поддержки вывода растра — "
                f"печать может выйти пустой"
            )

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
                # Режим растяжения задаём ПОСЛЕ StartPage: часть драйверов
                # сбрасывает атрибуты DC на каждой странице, и выставленное до
                # начала документа до бумаги не доезжает.
                _gdi32.SetStretchBltMode(hdc, COLORONCOLOR)
                if page_number is not None:
                    _emit_page(document, job, device, hdc, page_number, dpi, origin, support)
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
            pages_sent=len(order),
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
    support: dict[str, bool] | None = None,
) -> None:
    from ..geometry import build_sheet

    page_size = document.page_size(page_number)
    sheet = build_sheet(device, resolve_orientation(page_size, job))
    placement = compute_placement(page_size, sheet, job)
    bands = 0
    for band in iter_print_bands(document, page_number, placement, job, dpi):
        _blit(hdc, band.image, band.dest_px, origin, support)
        bands += 1
    logger.debug("Страница %d: %d полос, начало DC %s", page_number, bands, origin)
    if bands == 0:
        # Страница целиком за пределами области печати — на бумаге будет пусто,
        # и об этом лучше знать из журнала, чем из пустого листа.
        logger.warning(
            "Страница %d не дала ни одной полосы растра — содержимое вне области печати",
            page_number,
        )


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


def diagnose(printer_name: str, job: PrintJob | None = None) -> dict[str, object]:
    """Снимает с принтера всё, что нужно, чтобы понять, почему пусто на бумаге.

    Разделяет две разные беды, которые снаружи выглядят одинаково: «драйвер не
    принимает растр» и «мы кладём растр не туда».
    """
    import win32gui

    from ..printers.windows import build_devmode

    job = job or PrintJob(printer=printer_name)
    devmode, driver_copies = build_devmode(printer_name, job)
    hdc = int(win32gui.CreateDC("WINSPOOL", printer_name, devmode))
    if not hdc:
        raise PrintFailed(f"Не удалось открыть контекст принтера {printer_name!r}")
    try:
        caps = lambda index: _gdi32.GetDeviceCaps(hdc, index)  # noqa: E731
        geometry = device_geometry(hdc)
        return {
            "printer": printer_name,
            "technology": caps(TECHNOLOGY),
            "raster": raster_support(hdc),
            "dpi": {"x": caps(LOGPIXELSX), "y": caps(LOGPIXELSY)},
            "paper_px": {"width": caps(PHYSICALWIDTH), "height": caps(PHYSICALHEIGHT)},
            "printable_px": {"width": caps(HORZRES), "height": caps(VERTRES)},
            "offset_px": {"x": caps(PHYSICALOFFSETX), "y": caps(PHYSICALOFFSETY)},
            "paper_mm": {
                "width": round(geometry.paper_size.width / PT_PER_INCH * 25.4, 1),
                "height": round(geometry.paper_size.height / PT_PER_INCH * 25.4, 1),
            },
            "margins_mm": {
                "left": round(geometry.printable.x / PT_PER_INCH * 25.4, 1),
                "top": round(geometry.printable.y / PT_PER_INCH * 25.4, 1),
            },
            "driver_copies": driver_copies,
        }
    finally:
        _gdi32.DeleteDC(hdc)


def print_test_page(printer_name: str, job: PrintJob | None = None) -> PrintResult:
    """Печатает пробную страницу, минуя PDF целиком.

    Картинка рисуется здесь же, без PDFium, и уходит тем же путём, что и
    настоящая печать. Поэтому результат отвечает ровно на один вопрос: доносит
    ли наш вывод краску до бумаги.

    Вышел лист с рамкой и полосами — путь до принтера рабочий, и разбираться
    надо с документом. Вышел пустой — дело в выводе растра, и в журнале рядом
    видно, что именно ответил драйвер.
    """
    import win32gui
    from PIL import ImageDraw

    from ..printers.windows import build_devmode

    job = job or PrintJob(printer=printer_name)
    devmode, _ = build_devmode(printer_name, job)
    hdc = int(win32gui.CreateDC("WINSPOOL", printer_name, devmode))
    if not hdc:
        raise PrintFailed(f"Не удалось открыть контекст принтера {printer_name!r}")

    try:
        width = _gdi32.GetDeviceCaps(hdc, HORZRES)
        height = _gdi32.GetDeviceCaps(hdc, VERTRES)
        support = raster_support(hdc)
        logger.info(
            "Пробная страница: область печати %dx%d px, растр — %s",
            width, height,
            ", ".join(f"{k}: {'да' if v else 'нет'}" for k, v in support.items()),
        )

        image = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        thickness = max(4, height // 200)
        # Рамка по краю области печати: сразу видно, попали ли мы в лист и не
        # срезаны ли края.
        draw.rectangle([0, 0, width - 1, height - 1], outline=(0, 0, 0), width=thickness)
        # Диагонали — покажут поворот и зеркальность, если что-то перепутано.
        draw.line([0, 0, width - 1, height - 1], fill=(0, 0, 0), width=thickness)
        draw.line([width - 1, 0, 0, height - 1], fill=(0, 0, 0), width=thickness)
        # Полосы серого: по ним видно, что растр не «схлопнулся» в чёрное.
        for index, shade in enumerate((0, 64, 128, 192)):
            top = height // 3 + index * height // 24
            draw.rectangle(
                [width // 4, top, width * 3 // 4, top + height // 32],
                fill=(shade, shade, shade),
            )

        docinfo = DOCINFOW()
        docinfo.cbSize = ctypes.sizeof(DOCINFOW)
        docinfo.lpszDocName = "PbReader: пробная страница"
        docinfo.lpszOutput = None
        docinfo.lpszDatatype = None
        docinfo.fwType = 0

        job_id = _gdi32.StartDocW(hdc, ctypes.byref(docinfo))
        if job_id <= 0:
            raise PrintFailed(f"Принтер не принял задание (ошибка {ctypes.get_last_error()})")
        try:
            if _gdi32.StartPage(hdc) <= 0:
                raise PrintFailed("Не удалось начать страницу")
            _gdi32.SetStretchBltMode(hdc, COLORONCOLOR)
            _blit(hdc, image, (0, 0, width, height), (0, 0), support)
            if _gdi32.EndPage(hdc) <= 0:
                raise PrintFailed("Не удалось завершить страницу")
        except Exception:
            _gdi32.AbortDoc(hdc)
            raise
        if _gdi32.EndDoc(hdc) <= 0:
            raise PrintFailed("Принтер не закрыл задание")

        return PrintResult(
            printer=printer_name, sheets=1, pages_printed=1,
            dpi=_gdi32.GetDeviceCaps(hdc, LOGPIXELSX), pages_sent=1, job_id=job_id,
        )
    finally:
        _gdi32.DeleteDC(hdc)
