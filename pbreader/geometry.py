"""
Геометрия листа: физическая бумага, печатаемая область и «логический лист».

Здесь живёт разделение, на котором держится вся библиотека:

**Физический лист (paper space)** — то, что реально едет через принтер. Он
ВСЕГДА книжный: A4 210×297, подача короткой стороной. Начало координат — левый
верхний угол бумаги.

**Логический лист (sheet space)** — то, что человек видит в предпросмотре и
считает «страницей». При альбомной ориентации он повёрнут относительно
физического на 90°.

Почему не отдать поворот драйверу через DEVMODE.Orientation: Canon UFR II этот
флаг трактует по-своему, и документ уходит книжным, даже когда запрошен
альбомный. Поэтому поворот делаем сами, растром, а драйверу всегда отдаём
книжный лист — тогда интерпретировать ему нечего.

Печатаемая область берётся у драйвера (GetDeviceCaps) и переносится в логический
лист поворотом НАЗАД. Это важно: поля у принтера несимметричны (край подачи
обычно шире), и если бы мы просто считали их одинаковыми, предпросмотр показывал
бы поля не там, где они окажутся на бумаге.
"""

from __future__ import annotations

from dataclasses import dataclass

from .job import Orientation
from .paper import Paper
from .units import Rect, Size, mm_to_pt, rotate_rect

#: Непечатаемое поле, когда реальные данные принтера недоступны (предпросмотр на
#: машине без этого принтера, тесты). 4.2 мм — типичное значение и для Canon
#: LBP722, и для HP M507; настоящие цифры всё равно спрашиваем у драйвера.
NOMINAL_MARGIN_PT = mm_to_pt(4.2)


@dataclass(frozen=True)
class DeviceGeometry:
    """Что принтер сообщает о листе — в пойнтах, в системе физического листа.

    `is_measured=False` означает, что печатаемая область не измерена, а взята
    номинальной. Предпросмотр показывает это отдельной пометкой: поля могут
    оказаться на пару миллиметров другими.
    """

    paper_size: Size
    printable: Rect
    dpi_x: float = 600.0
    dpi_y: float = 600.0
    is_measured: bool = True

    @classmethod
    def nominal(cls, paper: Paper, margin_pt: float = NOMINAL_MARGIN_PT) -> "DeviceGeometry":
        return cls(
            paper_size=paper.size,
            printable=Rect.from_size(paper.size).inset(margin_pt, margin_pt),
            is_measured=False,
        )

    @property
    def dpi(self) -> float:
        """Одно разрешение для расчётов. Берём меньшее — так ничего не обрежется."""
        return min(self.dpi_x, self.dpi_y)


@dataclass(frozen=True)
class SheetGeometry:
    """Логический лист + правило переноса на физический."""

    #: Размер логического листа (при альбомной ориентации стороны переставлены).
    size: Size
    #: Печатаемая область в координатах ЛОГИЧЕСКОГО листа.
    printable: Rect
    #: Поворот по часовой, переводящий логический лист в физический.
    emit_rotation: int
    device: DeviceGeometry

    @property
    def paper_size(self) -> Size:
        return self.device.paper_size

    def to_paper(self, rect: Rect) -> Rect:
        """Переносит прямоугольник из логического листа на физическую бумагу."""
        return rotate_rect(rect, self.emit_rotation, self.size)

    def margins_mm(self) -> tuple[float, float, float, float]:
        """Поля логического листа (left, top, right, bottom) в мм — для UI."""
        to_mm = lambda pt: pt / 72.0 * 25.4  # noqa: E731
        return (
            to_mm(self.printable.x),
            to_mm(self.printable.y),
            to_mm(self.size.width - self.printable.right),
            to_mm(self.size.height - self.printable.bottom),
        )


def build_sheet(device: DeviceGeometry, orientation: Orientation) -> SheetGeometry:
    """Строит логический лист под запрошенную ориентацию.

    Orientation.AUTO сюда доходить не должен — его разрешает layout по первой
    странице документа, до вызова этой функции.
    """
    if orientation is Orientation.AUTO:
        raise ValueError("Orientation.AUTO должен быть разрешён до построения листа")

    if orientation is Orientation.PORTRAIT:
        return SheetGeometry(
            size=device.paper_size,
            printable=device.printable,
            emit_rotation=0,
            device=device,
        )

    # Альбомный лист — это физический лист, повёрнутый против часовой на 90°.
    # Печатаемую область переносим тем же поворотом, чтобы несимметричные поля
    # оказались с той стороны, с которой они реально будут на бумаге.
    sheet_size = device.paper_size.swapped()
    printable = rotate_rect(device.printable, 270, device.paper_size)
    return SheetGeometry(
        size=sheet_size,
        printable=printable,
        emit_rotation=90,
        device=device,
    )
