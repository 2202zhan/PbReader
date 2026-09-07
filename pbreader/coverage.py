"""
Заполнение страницы: сколько тонера уйдёт на лист.

Зачем. Аппарат берёт деньги за лист, а тонер тратится не за лист, а за краску
на нём. Обычная страница текста закрашена процентов на пять; сплошной чёрный
квадрат — на сто. Разница в двадцать раз, и её можно устроить нарочно: принести
один файл с чёрными страницами и высадить картридж по цене обычной печати.
Поэтому заполнение считается ДО оплаты и по нему можно либо брать другую цену,
либо отказать.

Что именно считается. Не «цветная страница или нет», а доля краски на ЛИСТЕ —
после масштаба, поворота и обрезки по области печати. Это важно: одна и та же
картинка, вписанная в лист целиком и уменьшенная до четверти, стоит разного
тонера, и считать надо то, что реально ляжет на бумагу.

Насколько это точно. Это оценка, соотнесённая с расходом, а не измерение
граммов. Принтер кладёт тонер точками через растрирование, у него своя
нелинейность, и одна и та же цифра на Canon и на HP обернётся немного разным
расходом. Но отличить страницу текста от залитой чёрным она позволяет с
огромным запасом — а больше для защиты от такого и не нужно.

За единицу отсчёта взята страница с заполнением 5 % — та самая, по которой
производители считают ресурс картриджа. Поэтому `ink_units` читается прямо:
«этот лист обойдётся как 14 обычных».
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageChops, ImageStat

from .document import PdfDocument
from .geometry import DeviceGeometry, build_sheet
from .job import ColorMode, PrintJob
from .layout import Placement, compute_placement, resolve_orientation
from .units import PT_PER_INCH

logger = logging.getLogger(__name__)

#: Заполнение «обычной» страницы. По этой величине производители считают ресурс
#: картриджа, поэтому она и взята за единицу.
REFERENCE_COVERAGE = 0.05

#: Плотность растеризации для замера. Заполнение — это среднее по площади, ему
#: высокое разрешение не нужно: на 100 dpi лист A4 это меньше миллиона точек,
#: то есть сотня страниц считается за секунды.
MEASURE_DPI = 100


@dataclass(frozen=True)
class PageCoverage:
    """Сколько краски уйдёт на одну сторону листа."""

    page: int | None
    #: Доля закрашенного, 0..1 — то самое «страница заполнена на 70 %».
    coverage: float
    #: Суммарный тонер: для ч/б 0..1, для цвета 0..4 (четыре красителя).
    ink: float
    #: Разложение по красителям для цветной печати; для ч/б — None.
    channels: dict[str, float] | None = None

    @property
    def percent(self) -> float:
        return round(self.coverage * 100, 1)

    @property
    def ink_units(self) -> float:
        """Во сколько обычных страниц обходится лист."""
        return round(self.ink / REFERENCE_COVERAGE, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "coverage": round(self.coverage, 4),
            "percent": self.percent,
            "ink": round(self.ink, 4),
            "ink_units": self.ink_units,
            "channels": {k: round(v, 4) for k, v in self.channels.items()} if self.channels else None,
        }


@dataclass
class DocumentCoverage:
    """Заполнение по всему заданию."""

    pages: list[PageCoverage] = field(default_factory=list)
    #: Замерена ли каждая страница документа. False — часть оценена по выборке.
    #: К копиям это не относится: они учитываются пересчётом, а не замером.
    complete: bool = True
    measured_pages: int = 0
    total_pages: int = 0

    @property
    def average(self) -> float:
        return sum(p.coverage for p in self.pages) / len(self.pages) if self.pages else 0.0

    @property
    def maximum(self) -> float:
        return max((p.coverage for p in self.pages), default=0.0)

    @property
    def heaviest(self) -> PageCoverage | None:
        return max(self.pages, key=lambda p: p.coverage, default=None)

    @property
    def total_ink_units(self) -> float:
        """Во сколько обычных страниц обходится задание целиком."""
        measured = sum(p.ink for p in self.pages)
        if self.pages and self.measured_pages and self.measured_pages < self.total_pages:
            # Часть страниц оценена по выборке — доводим до полного объёма.
            measured *= self.total_pages / self.measured_pages
        return round(measured / REFERENCE_COVERAGE, 2)

    def over(self, limit: float) -> list[PageCoverage]:
        """Страницы, закрашенные сильнее порога."""
        return [p for p in self.pages if p.coverage > limit]

    def to_dict(self) -> dict[str, Any]:
        heaviest = self.heaviest
        return {
            "average": round(self.average, 4),
            "average_percent": round(self.average * 100, 1),
            "maximum": round(self.maximum, 4),
            "maximum_percent": round(self.maximum * 100, 1),
            "heaviest_page": heaviest.page if heaviest else None,
            "total_ink_units": self.total_ink_units,
            "complete": self.complete,
            "measured_pages": self.measured_pages,
            "total_pages": self.total_pages,
            "pages": [p.to_dict() for p in self.pages],
        }


def ink_from_image(image: Image.Image, color: bool, area_ratio: float) -> tuple[float, float, dict | None]:
    """Считает краску по картинке.

    `area_ratio` — какую долю области печати занимает эта картинка. Незакрытая
    часть листа белая и краски не требует, поэтому средние по картинке
    приводятся к площади всего листа: иначе маленькая чёрная марка в углу
    выглядела бы страницей, залитой чёрным.
    """
    if image.width == 0 or image.height == 0:
        return 0.0, 0.0, None

    # Заполнение — это видимая чернота: ровно то, что человек называет
    # «страница закрашена на столько-то процентов».
    luminance = ImageStat.Stat(image.convert("L")).mean[0]
    coverage = (1.0 - luminance / 255.0) * area_ratio

    if not color:
        return coverage, coverage, None

    rgb = image.convert("RGB")
    red, green, blue = rgb.split()
    means = [ImageStat.Stat(channel).mean[0] for channel in (red, green, blue)]

    # Чёрный краситель: лазерный аппарат печатает чёрное чёрным тонером, а не
    # смешивает три цветных. Без этого сплошной чёрный лист насчитал бы
    # четырёхкратный расход вместо однократного.
    #
    # Чёрного столько, сколько есть во ВСЕХ трёх красителях сразу, то есть
    # min(c, m, y) = 1 - max(r, g, b). Отсюда нужен САМЫЙ СВЕТЛЫЙ канал: по
    # самому тёмному чистый красный (255,0,0) дал бы сплошной чёрный вместо
    # пурпурного с жёлтым.
    lightest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    black = (1.0 - ImageStat.Stat(lightest).mean[0] / 255.0) * area_ratio

    channels = {
        "c": max(0.0, (1.0 - means[0] / 255.0) * area_ratio - black),
        "m": max(0.0, (1.0 - means[1] / 255.0) * area_ratio - black),
        "y": max(0.0, (1.0 - means[2] / 255.0) * area_ratio - black),
        "k": black,
    }
    return coverage, sum(channels.values()), channels


def measure_placement(
    document: PdfDocument,
    page_number: int,
    placement: Placement,
    job: PrintJob,
    dpi: int = MEASURE_DPI,
) -> PageCoverage:
    """Считает заполнение одной стороны листа — по тому, что реально напечатается."""
    dest = placement.to_paper()
    printable = placement.sheet.device.printable
    visible = dest.intersection(printable)

    printable_px = (printable.width / PT_PER_INCH * dpi) * (printable.height / PT_PER_INCH * dpi)
    if visible.width <= 0 or visible.height <= 0 or printable_px <= 0:
        return PageCoverage(page=page_number, coverage=0.0, ink=0.0)

    scale = placement.scale or 1.0
    crop = (
        max(0.0, (visible.x - dest.x) / scale),
        max(0.0, (dest.bottom - visible.bottom) / scale),
        max(0.0, (dest.right - visible.right) / scale),
        max(0.0, (visible.y - dest.y) / scale),
    )
    is_color = job.color is ColorMode.COLOR
    image = document.render(
        page_number,
        dpi=dpi * scale,
        rotation=placement.total_rotation,
        crop_pt=crop,
        grayscale=not is_color,
    )

    area_ratio = min(1.0, (image.width * image.height) / printable_px)
    coverage, ink, channels = ink_from_image(image, is_color, area_ratio)
    return PageCoverage(page=page_number, coverage=coverage, ink=ink, channels=channels)


def measure_job(
    document: PdfDocument,
    job: PrintJob,
    device: DeviceGeometry,
    dpi: int = MEASURE_DPI,
    max_pages: int | None = 60,
) -> DocumentCoverage:
    """Считает заполнение по всему заданию — с учётом копий.

    `max_pages` ограничивает число замеров: у документа в тысячу страниц считать
    каждую значит заставить человека ждать у аппарата. Сверх предела берётся
    равномерная выборка, а итог доводится до полного объёма — для решения
    «пропускать или нет» этого достаточно, и результат помечается как неполный.
    """
    pages = job.resolve_pages(document.page_count)
    total = len(pages)

    sampled = pages
    complete = True
    if max_pages and total > max_pages:
        step = total / max_pages
        sampled = [pages[int(i * step)] for i in range(max_pages)]
        complete = False
        logger.info("Заполнение считается по выборке: %d страниц из %d", len(sampled), total)

    measured: list[PageCoverage] = []
    for page_number in sampled:
        page_size = document.page_size(page_number)
        sheet = build_sheet(device, resolve_orientation(page_size, job))
        placement = compute_placement(page_size, sheet, job)
        measured.append(measure_placement(document, page_number, placement, job, dpi))

    # Копии умножают расход ровно во столько же раз, поэтому в общий объём
    # входят все стороны задания, а замеров остаётся столько, сколько сделано:
    # итог доводится до объёма тем же пересчётом, что и выборка.
    return DocumentCoverage(
        pages=measured,
        complete=complete,
        measured_pages=len(measured),
        total_pages=total * job.copies,
    )


@dataclass(frozen=True)
class CoverageLimits:
    """Порог, за которым задание считается злоупотреблением.

    По умолчанию всё выключено: заполнение сначала просто показывается, и
    оператор решает, где провести черту, глядя на настоящие задания своего
    аппарата. Порог, выставленный наугад, отказал бы человеку с фотографией.
    """

    #: Доля закрашенного на одной стороне, 0..1. None — не проверять.
    max_page_coverage: float | None = None
    #: Расход на всё задание в обычных страницах. None — не проверять.
    max_ink_units: float | None = None
    #: False — только предупреждать, печать не останавливать.
    enforce: bool = False

    @classmethod
    def from_config(cls, config: Any) -> "CoverageLimits":
        page = getattr(config, "max_page_coverage", 0.0) or 0.0
        units = getattr(config, "max_ink_units", 0.0) or 0.0
        return cls(
            max_page_coverage=page if page > 0 else None,
            max_ink_units=units if units > 0 else None,
            enforce=bool(getattr(config, "enforce_coverage_limit", False)),
        )

    @property
    def active(self) -> bool:
        return self.max_page_coverage is not None or self.max_ink_units is not None


@dataclass
class CoverageVerdict:
    """Что делать с заданием: пропускать, предупредить или отказать."""

    allowed: bool = True
    #: Нарушенные пороги — человеческим языком.
    violations: list[str] = field(default_factory=list)
    enforced: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "violations": self.violations, "enforced": self.enforced}


def check_limits(coverage: DocumentCoverage, limits: CoverageLimits) -> CoverageVerdict:
    """Сверяет задание с порогами.

    Отказ — это `allowed=False`, и он возникает только при `enforce=True`.
    Иначе нарушения перечисляются, но печать идёт: заполнение бывает большим и
    у честного документа — фотография закрашена почти целиком.
    """
    violations: list[str] = []

    if limits.max_page_coverage is not None:
        heavy = coverage.over(limits.max_page_coverage)
        if heavy:
            worst = max(heavy, key=lambda p: p.coverage)
            pages = ", ".join(str(p.page) for p in heavy[:5])
            tail = f" и ещё {len(heavy) - 5}" if len(heavy) > 5 else ""
            violations.append(
                f"Заполнение выше {limits.max_page_coverage * 100:.0f} % на страницах {pages}{tail} "
                f"(самая тёмная — {worst.percent:.0f} %)"
            )

    if limits.max_ink_units is not None and coverage.total_ink_units > limits.max_ink_units:
        violations.append(
            f"Задание расходует тонера как {coverage.total_ink_units:.0f} обычных страниц "
            f"при разрешённых {limits.max_ink_units:.0f}"
        )

    return CoverageVerdict(
        allowed=not (violations and limits.enforce),
        violations=violations,
        enforced=limits.enforce,
    )
