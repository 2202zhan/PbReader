"""
Предпросмотр: PNG листа ровно таким, каким он выйдет из аппарата.

Отличие от того, что показывает PrintBox сейчас: сейчас это рендер PDF-страницы,
то есть картинка документа. Здесь — картинка ЛИСТА БУМАГИ: с полями принтера, с
применённым масштабом и поворотом, в чёрно-белом виде, если выбрано ч/б, и с
честно показанной обрезкой, если документ не помещается.

Ничего из этого предпросмотр не решает сам: и раскладку, и обрезку ему считает
pbreader.layout — тот же модуль, по которому потом печатается лист.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw

from .coverage import REFERENCE_COVERAGE, ink_from_image
from .document import PdfDocument
from .geometry import DeviceGeometry, build_sheet
from .job import ColorMode, PrintJob, ScaleMode
from .layout import Placement, compute_placement, resolve_orientation
from .raster import render_for_preview
from .sheets import Sheet, plan_sheets
from .units import PT_PER_INCH, Rect

#: Ширина картинки предпросмотра по умолчанию, пиксели.
DEFAULT_PREVIEW_WIDTH = 900
MAX_PREVIEW_WIDTH = 2400

#: Всё темнее этого порога считается краской. 250, а не 255 — сглаженный край
#: буквы и слабая заливка тоже краска, а вот шум растеризации в 253 — уже нет.
INK_THRESHOLD = 250

#: Доля тёмных пикселей, начиная с которой обрезка считается настоящей потерей.
#: Одиночные точки на границе — это сглаживание, а не потерянный текст.
#: Замеры: у документа, где терять нечего, за границей 0 %; у вылезающего —
#: от 2.7 % при любом размере картинки. Порог с запасом в двадцать раз.
INK_LOSS_FRACTION = 0.001

#: Полоска у самой границы области печати не проверяется: при уменьшении
#: картинки сглаживание размазывает содержимое на пиксель наружу, и без этого
#: запаса маленький предпросмотр объявлял потерю там, где её нет.
BLEED_DIVISOR = 250

_PAPER_BORDER = (176, 178, 182)
_MARGIN_GUIDE = (176, 178, 182)
_CLIP_WARNING = (214, 69, 69)
_FADE_STRENGTH = 0.78


@dataclass
class SheetPreview:
    """Картинка листа плюс всё, что о нём стоит сказать пользователю."""

    png: bytes
    width: int
    height: int
    sheet_number: int
    side: str  # front | back
    page: int | None  # страница PDF на этой стороне, None — чистая сторона
    scale_percent: float
    scale_mode: str
    is_clipped: bool  # геометрически вылезает за область печати
    ink_clipped: bool  # и в обрезаемой части действительно есть изображение
    #: Доля закрашенного на этой стороне, 0..1 — сколько тонера она стоит.
    coverage: float = 0.0
    #: То же в «обычных страницах»: 1.0 — как страница текста.
    ink_units: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def meta(self) -> dict[str, Any]:
        data = {k: v for k, v in self.__dict__.items() if k != "png"}
        return data


def _count_ink(image: Image.Image, box: tuple[int, int, int, int]) -> int:
    """Сколько в области тёмных пикселей."""
    left, top, right, bottom = box
    left, top = max(0, left), max(0, top)
    right, bottom = min(image.width, right), min(image.height, bottom)
    if right <= left or bottom <= top:
        return 0
    region = image.crop((left, top, right, bottom)).convert("L")
    return sum(region.histogram()[:INK_THRESHOLD])


def _has_ink(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    """Есть ли в области хоть что-то, кроме белого."""
    return _count_ink(image, box) > 0


def _fade(image: Image.Image, keep: tuple[int, int, int, int]) -> None:
    """Осветляет всё за пределами `keep` — то, что принтер не напечатает.

    Не вырезаем совсем: человек должен увидеть, ЧТО именно он теряет, иначе
    предпросмотр врёт умолчанием.
    """
    white = Image.new("RGB", image.size, (255, 255, 255))
    faded = Image.blend(image, white, _FADE_STRENGTH)
    faded.paste(image.crop(keep), (keep[0], keep[1]))
    image.paste(faded)


def _dashed_rect(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], color, dash: int = 6) -> None:
    left, top, right, bottom = box
    for x in range(left, right, dash * 2):
        draw.line([(x, top), (min(x + dash, right), top)], fill=color)
        draw.line([(x, bottom), (min(x + dash, right), bottom)], fill=color)
    for y in range(top, bottom, dash * 2):
        draw.line([(left, y), (left, min(y + dash, bottom))], fill=color)
        draw.line([(right, y), (right, min(y + dash, bottom))], fill=color)


def _px_rect(rect: Rect, dpi: float) -> tuple[int, int, int, int]:
    left, top, width, height = rect.rounded_px(dpi)
    return left, top, left + width, top + height


def render_sheet_side(
    document: PdfDocument,
    job: PrintJob,
    device: DeviceGeometry,
    sheet: Sheet,
    side: str = "front",
    width_px: int = DEFAULT_PREVIEW_WIDTH,
    show_margins: bool = True,
) -> SheetPreview:
    """Рисует одну сторону одного листа."""
    width_px = max(120, min(int(width_px), MAX_PREVIEW_WIDTH))
    page_number = sheet.side(side).page

    orientation = resolve_orientation(
        document.page_size(page_number) if page_number else document.page_size(1), job
    )
    sheet_geometry = build_sheet(device, orientation)
    dpi = width_px / sheet_geometry.size.width * PT_PER_INCH

    sheet_w = max(1, int(round(sheet_geometry.size.width / PT_PER_INCH * dpi)))
    sheet_h = max(1, int(round(sheet_geometry.size.height / PT_PER_INCH * dpi)))
    canvas = Image.new("RGB", (sheet_w, sheet_h), (255, 255, 255))

    warnings: list[str] = []
    if not device.is_measured:
        warnings.append(
            "Поля показаны приблизительно: принтер недоступен, взяты типовые 4.2 мм"
        )

    placement: Placement | None = None
    ink_clipped = False
    coverage = 0.0
    ink = 0.0

    if page_number is not None:
        placement = compute_placement(document.page_size(page_number), sheet_geometry, job)
        raster = render_for_preview(document, page_number, placement, job, dpi)
        canvas.paste(raster.image, (raster.dest_px[0], raster.dest_px[1]))

        printable_px = _px_rect(sheet_geometry.printable, dpi)
        # Заполнение считаем по уже нарисованному листу и ДО осветления
        # обрезаемых полей: растр всё равно построен, отдельный проход не нужен.
        left, top, right, bottom = printable_px
        coverage, ink, _ = ink_from_image(
            canvas.crop((max(0, left), max(0, top), min(canvas.width, right), min(canvas.height, bottom))),
            job.color is ColorMode.COLOR,
            1.0,
        )

        if placement.is_clipped:
            ink_clipped = _ink_lost(raster.image, raster.dest_px, printable_px)
            _fade(canvas, printable_px)
            if ink_clipped:
                warnings.append(
                    "Часть документа выходит за область печати и не напечатается — "
                    "выберите «вписать в лист»"
                )

    draw = ImageDraw.Draw(canvas)
    if show_margins:
        left, top, right, bottom = _px_rect(sheet_geometry.printable, dpi)
        if ink_clipped:
            # Пунктир — это подсказка «здесь край области печати». Когда за ним
            # реально теряется документ, подсказки мало: рамка становится
            # сплошной и заметной, потому что это уже не справка, а
            # предупреждение.
            draw.rectangle([(left, top), (right, bottom)], outline=_CLIP_WARNING, width=2)
        else:
            _dashed_rect(draw, (left, top, right, bottom), _MARGIN_GUIDE)
    draw.rectangle([(0, 0), (sheet_w - 1, sheet_h - 1)], outline=_PAPER_BORDER)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)

    return SheetPreview(
        png=buffer.getvalue(),
        width=sheet_w,
        height=sheet_h,
        sheet_number=sheet.number,
        side=side,
        page=page_number,
        scale_percent=round((placement.scale if placement else 1.0) * 100, 1),
        scale_mode=(placement.resolved_scale_mode if placement else ScaleMode.ACTUAL).value,
        is_clipped=bool(placement and placement.is_clipped),
        ink_clipped=ink_clipped,
        coverage=round(coverage, 4),
        ink_units=round(ink / REFERENCE_COVERAGE, 2),
        warnings=warnings,
    )


def _ink_lost(page: Image.Image, dest_px: tuple[int, int, int, int], printable_px: tuple[int, int, int, int]) -> bool:
    """Есть ли изображение в той части страницы, которая не напечатается.

    Проверяется РАСТР СТРАНИЦЫ, а не собранный лист: содержимое, ушедшее за
    край бумаги целиком, на лист вообще не попадает, и по листу его потерю не
    увидеть — а это как раз самый тяжёлый случай обрезки.

    Различать «вылезает» и «теряется изображение» обязательно: страница A4,
    напечатанная один к одному, ВСЕГДА выходит на непечатаемые поля — там
    просто ничего нет. Ругаться на это каждый раз значит приучить человека не
    читать предупреждения.

    Ответ обязан быть одинаковым при любом размере картинки: один и тот же
    документ не может «терять текст» в миниатюре и не терять в полном виде.
    Поэтому у границы оставляется запас на сглаживание, а редкие одиночные
    точки не считаются потерей.
    """
    left, top, width, height = dest_px
    bleed = max(1, round(max(page.width, page.height) / BLEED_DIVISOR))

    # Печатаемая область в координатах самого растра страницы, расширенная на
    # запас: то, что попало в этот запас, — след сглаживания, а не потеря.
    keep = (
        max(0, printable_px[0] - left) - bleed,
        max(0, printable_px[1] - top) - bleed,
        min(page.width, printable_px[2] - left) + bleed,
        min(page.height, printable_px[3] - top) + bleed,
    )
    if keep[2] <= keep[0] or keep[3] <= keep[1]:
        # Не видно вообще ничего — потеряется всё, что на странице нарисовано.
        return _has_ink(page, (0, 0, page.width, page.height))

    strips = (
        (0, 0, page.width, keep[1]),
        (0, keep[3], page.width, page.height),
        (0, keep[1], keep[0], keep[3]),
        (keep[2], keep[1], page.width, keep[3]),
    )
    lost = sum(_count_ink(page, strip) for strip in strips)
    return lost > page.width * page.height * INK_LOSS_FRACTION


def describe_job(
    document: PdfDocument,
    job: PrintJob,
    device: DeviceGeometry,
) -> dict[str, Any]:
    """Полное описание задания для интерфейса — до того, как что-то напечатано.

    Отдаёт то, чего у PrintBox сейчас нет: сколько РЕАЛЬНО выйдет листов (не
    страниц), что окажется на каждой стороне, какой масштаб применится и не
    потеряется ли часть документа.
    """
    pages = job.resolve_pages(document.page_count)
    plan = plan_sheets(pages, job.duplex, job.copies, job.collate)

    first_size = document.page_size(pages[0])
    orientation = resolve_orientation(first_size, job)
    sheet_geometry = build_sheet(device, orientation)
    placement = compute_placement(first_size, sheet_geometry, job)

    left, top, right, bottom = sheet_geometry.margins_mm()
    return {
        "document": {
            "name": document.path.name,
            "page_count": document.page_count,
        },
        "job": job.to_dict(),
        "pages": pages,
        "orientation": orientation.value,
        "sheets": [
            {
                "number": s.number,
                "copy": s.copy,
                "front": s.front.page,
                "back": s.back.page if s.back else None,
            }
            for s in plan
        ],
        "sheet_count": len(plan),
        "paper": {
            "name": job.paper.name,
            "width_mm": round(sheet_geometry.size.width / PT_PER_INCH * 25.4, 1),
            "height_mm": round(sheet_geometry.size.height / PT_PER_INCH * 25.4, 1),
            "margins_mm": {
                "left": round(left, 1),
                "top": round(top, 1),
                "right": round(right, 1),
                "bottom": round(bottom, 1),
            },
            "margins_measured": device.is_measured,
        },
        "scale": {
            "mode": placement.resolved_scale_mode.value,
            "percent": round(placement.scale * 100, 1),
            "clipped": placement.is_clipped,
        },
    }
