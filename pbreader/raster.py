"""
Растеризация раскладки: Placement → пиксели.

Один и тот же расчёт обслуживает и предпросмотр, и печать — меняется только
плотность (96 dpi против 600) и то, куда уходит результат (PNG против DC
принтера). Отсюда и берётся совпадение картинки с бумагой.

Печать идёт ПОЛОСАМИ. Лист A4 при 600 dpi — это 4960×7016 пикселей, то есть
104 МБ в RGB, а PDFium внутри держит ещё и свой BGRA-буфер. На аппарате, где
одновременно крутится Electron и браузер, такой пик памяти на каждой странице
многостраничного задания — реальный риск, поэтому страница режется на полосы и
уходит в принтер по частям.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from PIL import Image

from .document import PdfDocument
from .job import ColorMode, PrintJob
from .layout import Placement
from .units import PT_PER_INCH, Rect

#: Сколько памяти отдаём под одну полосу растра (в байтах готовой RGB-картинки).
#: 24 МБ ≈ 8 Мпикс — полоса A4 высотой ~5 см при 600 dpi.
MAX_BAND_BYTES = 24 * 1024 * 1024

#: Разрешение печати по умолчанию. Для ч/б берём полное разрешение движка (текст
#: на 300 dpi заметно рыхлее), для цвета — вдвое меньше: цветной растр вчетверо
#: тяжелее, а разницу на 600 dpi глаз на обычном документе уже не ловит.
DEFAULT_PRINT_DPI = {ColorMode.MONOCHROME: 600, ColorMode.COLOR: 300}


@dataclass(frozen=True)
class Band:
    """Кусок растра и место на физической бумаге, куда он ложится."""

    image: Image.Image
    #: Прямоугольник назначения в пикселях устройства, от угла ФИЗИЧЕСКОЙ бумаги.
    dest_px: tuple[int, int, int, int]  # (left, top, width, height)


def print_dpi_for(job: PrintJob, device_dpi: float | None = None) -> int:
    """Разрешение растеризации для печати.

    Больше физического разрешения принтера смысла не имеет — лишние пиксели он
    всё равно выбросит, а память и время спулинга мы потратим.
    """
    dpi = job.print_dpi or DEFAULT_PRINT_DPI[job.color]
    if device_dpi:
        dpi = min(dpi, int(round(device_dpi)))
    return max(72, dpi)


def _render_dpi(placement: Placement, output_dpi: float) -> float:
    """Плотность растеризации страницы, дающая ровно нужный размер на бумаге."""
    return output_dpi * placement.scale


def _crop_for(placement: Placement, dest: Rect, visible: Rect) -> tuple[float, float, float, float]:
    """Сколько отрезать от повёрнутой страницы, чтобы остался только `visible`.

    Порядок и смысл — как у PDFium: (слева, снизу, справа, сверху) в пойнтах
    исходной страницы, уже после поворота.
    """
    scale = placement.scale or 1.0
    return (
        max(0.0, (visible.x - dest.x) / scale),
        max(0.0, (dest.bottom - visible.bottom) / scale),
        max(0.0, (dest.right - visible.right) / scale),
        max(0.0, (visible.y - dest.y) / scale),
    )


def iter_print_bands(
    document: PdfDocument,
    page_number: int,
    placement: Placement,
    job: PrintJob,
    dpi: int,
    max_band_bytes: int = MAX_BAND_BYTES,
) -> Iterator[Band]:
    """Готовит страницу к выводу на принтер: обрезает по области печати и режет на полосы.

    Всё, что вылезает за печатаемую область, не растеризуется вовсе: принтер это
    всё равно отрежет, а на «как есть» с большим документом невидимая часть может
    быть больше видимой.
    """
    dest = placement.to_paper()
    printable = placement.sheet.device.printable
    visible = dest.intersection(printable)
    if visible.width <= 0 or visible.height <= 0:
        return

    grayscale = job.color is ColorMode.MONOCHROME
    render_dpi = _render_dpi(placement, dpi)
    left_px, top_px, width_px, height_px = visible.rounded_px(dpi)

    px_per_pt = dpi / PT_PER_INCH
    bytes_per_row = max(1, width_px * 3)
    band_rows = max(1, min(height_px, max_band_bytes // bytes_per_row))

    y = 0
    while y < height_px:
        rows = min(band_rows, height_px - y)
        band_top_pt = visible.y + y / px_per_pt
        band = Rect(visible.x, band_top_pt, visible.width, rows / px_per_pt)
        crop = _crop_for(placement, dest, band)

        image = document.render(
            page_number,
            dpi=render_dpi,
            rotation=placement.total_rotation,
            crop_pt=crop,
            grayscale=grayscale,
        )
        # Размер растра PDFium округляет по-своему, поэтому целевой прямоугольник
        # задаём мы, а несовпадение в пиксель поглощает масштабирующий вывод
        # (StretchDIBits). Так между полосами не остаётся ни щели, ни нахлёста.
        yield Band(image=image, dest_px=(left_px, top_px + y, width_px, rows))
        y += rows


@dataclass(frozen=True)
class PreviewRaster:
    """Растр страницы для предпросмотра — целиком, без обрезки по полям."""

    image: Image.Image
    #: Куда картинка ложится на логическом листе, в пикселях предпросмотра.
    dest_px: tuple[int, int, int, int]
    sheet_px: tuple[int, int]


def render_for_preview(
    document: PdfDocument,
    page_number: int,
    placement: Placement,
    job: PrintJob,
    dpi: float,
) -> PreviewRaster:
    """Растеризует страницу целиком в координатах ЛОГИЧЕСКОГО листа.

    Обрезка тут намеренно не делается: предпросмотр должен показать и то, что
    НЕ напечатается, — иначе человек не поймёт, почему из аппарата вышел
    документ без края.
    """
    grayscale = job.color is ColorMode.MONOCHROME
    image = document.render(
        page_number,
        dpi=_render_dpi(placement, dpi),
        rotation=placement.content_rotation,
        grayscale=grayscale,
    )
    left, top, width, height = placement.dest.rounded_px(dpi)
    sheet = placement.sheet.size
    sheet_px = (
        max(1, int(round(sheet.width / PT_PER_INCH * dpi))),
        max(1, int(round(sheet.height / PT_PER_INCH * dpi))),
    )
    return PreviewRaster(image=image, dest_px=(left, top, width, height), sheet_px=sheet_px)
