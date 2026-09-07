"""
ЯДРО. Куда именно ляжет страница на листе.

Здесь нет ни PDF, ни принтера, ни картинок — только арифметика: на вход размер
страницы, лист и параметры задания, на выходе поворот, масштаб и прямоугольник.
Поэтому модуль полностью покрывается тестами без Windows и без бумаги, и
поэтому же предпросмотр не может разойтись с печатью: обе ветки зовут
compute_placement() и просто по-разному рисуют его результат.
"""

from __future__ import annotations

from dataclasses import dataclass

from .geometry import SheetGeometry
from .job import Orientation, PrintJob, ScaleMode
from .paper import SIZE_TOLERANCE_PT
from .units import Rect, Size, sizes_match

#: Насколько поворот должен улучшить масштаб, чтобы auto_rotate сработал.
#: Меньше 2 % — не повод крутить страницу, человека это только запутает.
AUTO_ROTATE_GAIN = 1.02


@dataclass(frozen=True)
class Placement:
    """Где и в каком виде содержимое страницы окажется на ЛОГИЧЕСКОМ листе."""

    #: Поворот содержимого по часовой (auto_rotate). Ориентация листа тут ни при
    #: чём — за неё отвечает SheetGeometry.emit_rotation.
    content_rotation: int
    scale: float
    #: Прямоугольник содержимого в координатах логического листа.
    dest: Rect
    #: Размер исходной страницы после content_rotation.
    source_size: Size
    #: Режим масштабирования после разрешения ScaleMode.AUTO — что применилось на самом деле.
    resolved_scale_mode: ScaleMode
    sheet: SheetGeometry

    @property
    def visible(self) -> Rect:
        """Часть содержимого, которая реально попадёт на бумагу."""
        return self.dest.intersection(self.sheet.printable)

    @property
    def is_clipped(self) -> bool:
        """True, если что-то из документа не напечатается."""
        return not self.sheet.printable.contains(self.dest, tolerance=0.5)

    @property
    def clipped_fraction(self) -> float:
        """Доля площади содержимого, которая теряется (0.0 — ничего не теряется)."""
        total = self.dest.width * self.dest.height
        if total <= 0:
            return 0.0
        visible = self.visible
        return max(0.0, 1.0 - (visible.width * visible.height) / total)

    def to_paper(self) -> Rect:
        """Прямоугольник содержимого на физической бумаге."""
        return self.sheet.to_paper(self.dest)

    @property
    def total_rotation(self) -> int:
        """Поворот при растеризации для ПЕЧАТИ: поворот содержимого + поворот листа."""
        return (self.content_rotation + self.sheet.emit_rotation) % 360


def resolve_orientation(page_size: Size, job: PrintJob) -> Orientation:
    """Разрешает Orientation.AUTO по странице документа."""
    if job.orientation is not Orientation.AUTO:
        return job.orientation
    return Orientation.LANDSCAPE if page_size.is_landscape else Orientation.PORTRAIT


def _fit_scale(source: Size, target: Rect) -> float:
    if source.width <= 0 or source.height <= 0:
        return 1.0
    return min(target.width / source.width, target.height / source.height)


def _fill_scale(source: Size, target: Rect) -> float:
    if source.width <= 0 or source.height <= 0:
        return 1.0
    return max(target.width / source.width, target.height / source.height)


def _resolve_scale_mode(source: Size, sheet: SheetGeometry, mode: ScaleMode) -> ScaleMode:
    """Разворачивает ScaleMode.AUTO в конкретный режим.

    Правило то же, к которому пришла прежняя реализация на SumatraPDF: если
    страница по размеру совпала с листом — печатаем 1:1, чтобы ничего не
    «поехало» на миллиметр; во всех остальных случаях вписываем, иначе у
    нестандартного документа обрежутся края.

    Совпадение считается с допуском в 2 мм и С УЧЁТОМ ориентации: альбомная
    A4-страница на КНИЖНОМ листе по ширине не влезает, и печатать её один к
    одному нельзя — обрежется треть документа. Ориентация листа здесь уже учтена,
    потому что sheet.size у альбомного листа с переставленными сторонами.
    """
    if mode is not ScaleMode.AUTO:
        return mode
    if sizes_match(source, sheet.size, SIZE_TOLERANCE_PT, ignore_orientation=False):
        return ScaleMode.ACTUAL
    return ScaleMode.FIT


def _scale_for(source: Size, sheet: SheetGeometry, mode: ScaleMode, custom: float) -> float:
    if mode is ScaleMode.ACTUAL:
        return 1.0
    if mode is ScaleMode.CUSTOM:
        return custom
    if mode is ScaleMode.FIT:
        return _fit_scale(source, sheet.printable)
    if mode is ScaleMode.SHRINK:
        return min(1.0, _fit_scale(source, sheet.printable))
    if mode is ScaleMode.FILL:
        return _fill_scale(source, sheet.printable)
    raise ValueError(f"Не разрешённый режим масштабирования: {mode}")


def _should_auto_rotate(page_size: Size, sheet: SheetGeometry) -> bool:
    """Стоит ли довернуть страницу на 90°, чтобы она легла крупнее."""
    straight = _fit_scale(page_size, sheet.printable)
    turned = _fit_scale(page_size.swapped(), sheet.printable)
    return turned > straight * AUTO_ROTATE_GAIN


def compute_placement(page_size: Size, sheet: SheetGeometry, job: PrintJob) -> Placement:
    """Считает раскладку одной страницы на листе.

    `page_size` — ВИДИМЫЙ размер страницы, то есть уже с учётом её /Rotate
    (PdfDocument.page_size отдаёт именно такой; см. document.py — на этом месте
    прежняя реализация ошибалась и печатала повёрнутые сканы боком).
    """
    content_rotation = 90 if (job.auto_rotate and _should_auto_rotate(page_size, sheet)) else 0
    source = page_size.rotated(content_rotation)

    mode = _resolve_scale_mode(source, sheet, job.scale)
    scale = _scale_for(source, sheet, mode, job.scale_factor)
    scaled = Rect.from_size(source.scaled(scale))

    # Режимы, которые гарантированно влезают (или намеренно обрезаются по краям
    # печатаемой области), центрируем по печатаемой области — иначе при
    # несимметричных полях вписанная страница вылезла бы за край с узкой
    # стороны. Режимы «как есть» центрируем по листу: человек, выбравший 100 %,
    # ждёт страницу по центру бумаги, а не по центру области печати.
    anchor = (
        sheet.printable
        if mode in (ScaleMode.FIT, ScaleMode.SHRINK, ScaleMode.FILL)
        else Rect.from_size(sheet.size)
    )
    dest = scaled.centered_in(anchor)

    return Placement(
        content_rotation=content_rotation,
        scale=scale,
        dest=dest,
        source_size=source,
        resolved_scale_mode=mode,
        sheet=sheet,
    )
