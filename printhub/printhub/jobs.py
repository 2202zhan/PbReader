"""
Мост между заказом и PbReader.

Здесь параметры из интерфейса превращаются в задание PbReader, считается
раскладка (сколько выйдет листов и что окажется на каждой стороне) и снимается
заполнение — то есть всё, что нужно, чтобы показать предпросмотр и посчитать
цену. Саму цену считает pricing.py: здесь только измерения.

Одно принципиальное место — геометрия листа. Настоящие поля печати знает
драйвер конкретного принтера, а его у нас пока нет. Поэтому берутся типовые и
помечаются `margins_measured: false`: интерфейс обязан сказать об этом, а не
показать поля, которых не измерял. Когда у аппарата появится агент, он пришлёт
свою геометрию, флаг станет true — и ни строчки в интерфейсе менять не придётся.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pbreader import PrintJob
from pbreader.coverage import measure_job
from pbreader.devices import resolve_device
from pbreader.document import PdfDocument
from pbreader.geometry import DeviceGeometry, Rect, Size
from pbreader.paper import get_paper
from pbreader.preview import DEFAULT_PREVIEW_WIDTH, describe_job, render_sheet_side
from pbreader.sheets import plan_sheets

#: Сколько замеров заполнения помнить. Замер стоит растеризации документа, а
#: интерфейс дёргает пересчёт на каждое переключение параметра.
CACHE_LIMIT = 64

_coverage_cache: dict[tuple, dict[int, float]] = {}
_cache_lock = threading.Lock()


def build_job(options: dict[str, Any], printer=None) -> PrintJob:
    """Собирает задание PbReader из параметров заказа.

    Возможности аппарата здесь же и ограничивают выбор: просить цвет у
    чёрно-белого принтера бессмысленно, а молча напечатать в ч/б, показав
    цветной предпросмотр и взяв цветную цену, — обман.
    """
    raw = dict(options or {})
    if printer is not None:
        raw["paper"] = printer.paper
        raw["printer"] = printer.id
        if not printer.color_supported:
            raw["color"] = "monochrome"
        if not printer.duplex_supported:
            raw["duplex"] = "simplex"
    return PrintJob.from_dict(raw)


def device_for(job: PrintJob, printer=None) -> DeviceGeometry:
    """Геометрия листа: настоящая, если аппарат её прислал, иначе типовая."""
    geometry = getattr(printer, "geometry", None)
    if geometry:
        paper = get_paper(job.paper.name)
        printable = geometry.get("printable", {})
        return DeviceGeometry(
            paper_size=Size(
                geometry.get("paper_width", paper.size.width),
                geometry.get("paper_height", paper.size.height),
            ),
            printable=Rect(
                printable.get("left", 0.0),
                printable.get("top", 0.0),
                printable.get("right", paper.size.width),
                printable.get("bottom", paper.size.height),
            ),
            dpi_x=geometry.get("dpi_x", 600.0),
            dpi_y=geometry.get("dpi_y", 600.0),
            is_measured=True,
        )
    # На сервере принтера нет, и PbReader сам вернёт типовую геометрию с
    # пометкой is_measured=False. Врать ему здесь нечем — и не надо.
    return resolve_device(job)


def _coverage_key(job: PrintJob, path: Path) -> tuple:
    """Что влияет на заполнение СТРАНИЦЫ.

    Копии и подборка не влияют: вторая копия закрашена ровно так же, как первая.
    Держать их в ключе значило бы мерить заново при каждом «+1 копия».
    """
    data = job.to_dict()
    for ignored in ("copies", "collate", "printer"):
        data.pop(ignored, None)
    return (str(path), tuple(sorted(data.items(), key=lambda kv: str(kv[0]))))


def page_coverages(document: PdfDocument, job: PrintJob, device: DeviceGeometry) -> dict[int, float]:
    key = _coverage_key(job, document.path)
    with _cache_lock:
        cached = _coverage_cache.get(key)
    if cached is not None:
        return cached

    measured = measure_job(document, job, device)
    result = {page.page: page.coverage for page in measured.pages if page.page is not None}
    # Документ длиннее предела меряется по выборке — недостающие страницы
    # оцениваем средним. Это честнее нуля: нулём мы бы просто не взяли денег.
    result["__average__"] = measured.average

    with _cache_lock:
        if len(_coverage_cache) >= CACHE_LIMIT:
            _coverage_cache.pop(next(iter(_coverage_cache)))
        _coverage_cache[key] = result
    return result


def forget_cached_coverage() -> None:
    with _cache_lock:
        _coverage_cache.clear()


@dataclass
class Plan:
    """Что выйдет из этих параметров — до того, как что-то напечатано."""

    sheet_count: int
    #: Заполнение каждой ЗАПЕЧАТАННОЙ стороны. Чистый оборот сюда не попадает:
    #: он не печатается, и брать за него деньги нельзя.
    side_coverages: list[float]
    #: Сколько сторон напечатано на листах, занятых с обеих сторон, — только
    #: они экономят бумагу и только на них даётся скидка за дуплекс.
    duplex_sides: int
    description: dict[str, Any]

    @property
    def printed_sides(self) -> int:
        return len(self.side_coverages)

    def to_dict(self) -> dict[str, Any]:
        description = dict(self.description)
        description["sheet_count"] = self.sheet_count
        description["printed_sides"] = self.printed_sides
        description["duplex_sides"] = self.duplex_sides
        description["ink"] = {
            "average_percent": round(
                100 * sum(self.side_coverages) / len(self.side_coverages), 1
            )
            if self.side_coverages
            else 0.0,
            "maximum_percent": round(100 * max(self.side_coverages, default=0.0), 1),
        }
        return description


def analyse(document: PdfDocument, job: PrintJob, device: DeviceGeometry) -> Plan:
    pages = job.resolve_pages(document.page_count)
    sheets = plan_sheets(pages, job.duplex, job.copies, job.collate)

    coverages = page_coverages(document, job, device)
    average = coverages.get("__average__", 0.0)

    side_coverages: list[float] = []
    duplex_sides = 0
    for sheet in sheets:
        printed = [
            side for side in (sheet.front, sheet.back)
            if side is not None and side.page is not None
        ]
        for side in printed:
            side_coverages.append(coverages.get(side.page, average))
        if len(printed) == 2:
            duplex_sides += 2

    return Plan(
        sheet_count=len(sheets),
        side_coverages=side_coverages,
        duplex_sides=duplex_sides,
        description=describe_job(document, job, device),
    )


def render(
    document: PdfDocument,
    job: PrintJob,
    device: DeviceGeometry,
    sheet_number: int,
    side: str = "front",
    width: int = DEFAULT_PREVIEW_WIDTH,
):
    pages = job.resolve_pages(document.page_count)
    sheets = plan_sheets(pages, job.duplex, job.copies, job.collate)
    if not 1 <= sheet_number <= len(sheets):
        raise IndexError(f"Листа {sheet_number} нет: в задании {len(sheets)} листов")
    return render_sheet_side(document, job, device, sheets[sheet_number - 1], side, width)
