"""
Единицы измерения и примитивы геометрии.

Внутри всей библиотеки размеры хранятся в PDF-пойнтах (1/72 дюйма) — это
"родная" единица PDF и одновременно та, в которой Windows отдаёт физические
размеры листа. Перевод в пиксели устройства происходит ровно в двух местах:
при растеризации страницы и при выводе на принтер, и оба используют один и тот
же коэффициент (см. pbreader.raster).

Система координат: начало в ЛЕВОМ ВЕРХНЕМ углу, ось Y вниз — как у картинки и
как у DC принтера. PDF внутри себя считает Y вверх, но за это отвечает PDFium,
наружу эта разница не протекает.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

PT_PER_INCH = 72.0
MM_PER_INCH = 25.4


def mm_to_pt(mm: float) -> float:
    return mm / MM_PER_INCH * PT_PER_INCH


def pt_to_mm(pt: float) -> float:
    return pt / PT_PER_INCH * MM_PER_INCH


def pt_to_px(pt: float, dpi: float) -> float:
    return pt / PT_PER_INCH * dpi


def px_to_pt(px: float, dpi: float) -> float:
    return px / dpi * PT_PER_INCH


@dataclass(frozen=True)
class Size:
    width: float
    height: float

    @property
    def is_landscape(self) -> bool:
        return self.width > self.height

    def swapped(self) -> "Size":
        return Size(self.height, self.width)

    def rotated(self, degrees: int) -> "Size":
        return self.swapped() if degrees % 180 == 90 else self

    def scaled(self, factor: float) -> "Size":
        return Size(self.width * factor, self.height * factor)

    def as_tuple(self) -> tuple[float, float]:
        return (self.width, self.height)


@dataclass(frozen=True)
class Rect:
    """Прямоугольник в системе координат «Y вниз»: (x, y) — левый верхний угол."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def size(self) -> Size:
        return Size(self.width, self.height)

    @classmethod
    def from_edges(cls, left: float, top: float, right: float, bottom: float) -> "Rect":
        return cls(left, top, right - left, bottom - top)

    @classmethod
    def from_size(cls, size: Size) -> "Rect":
        return cls(0.0, 0.0, size.width, size.height)

    def inset(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.width - 2 * dx, self.height - 2 * dy)

    def translated(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.width, self.height)

    def scaled(self, factor: float) -> "Rect":
        return Rect(self.x * factor, self.y * factor, self.width * factor, self.height * factor)

    def centered_in(self, outer: "Rect") -> "Rect":
        return Rect(
            outer.x + (outer.width - self.width) / 2.0,
            outer.y + (outer.height - self.height) / 2.0,
            self.width,
            self.height,
        )

    def intersection(self, other: "Rect") -> "Rect":
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return Rect(left, top, 0.0, 0.0)
        return Rect.from_edges(left, top, right, bottom)

    def contains(self, other: "Rect", tolerance: float = 1e-6) -> bool:
        return (
            other.x >= self.x - tolerance
            and other.y >= self.y - tolerance
            and other.right <= self.right + tolerance
            and other.bottom <= self.bottom + tolerance
        )

    def rounded_px(self, dpi: float) -> tuple[int, int, int, int]:
        """Прямоугольник в целых пикселях устройства: (left, top, width, height).

        Границы округляются, а не размер — иначе при сложении округлённых
        координат и округлённого размера правый край уезжает на пиксель.
        """
        left = int(round(pt_to_px(self.x, dpi)))
        top = int(round(pt_to_px(self.y, dpi)))
        right = int(round(pt_to_px(self.right, dpi)))
        bottom = int(round(pt_to_px(self.bottom, dpi)))
        return left, top, max(1, right - left), max(1, bottom - top)


def rotate_size(size: Size, degrees: int) -> Size:
    return size.rotated(degrees)


def rotate_rect(rect: Rect, degrees: int, space: Size) -> Rect:
    """Переносит прямоугольник в систему координат, повёрнутую на `degrees` по часовой.

    `space` — размер исходного пространства. Используется для перевода между
    «логическим листом» (то, что видит пользователь в предпросмотре) и
    физическим листом, который реально едет через принтер: при альбомной
    ориентации это одно и то же место, но повёрнутое на 90°.
    """
    degrees %= 360
    if degrees == 0:
        return rect
    w, h = space.width, space.height
    if degrees == 90:
        return Rect.from_edges(h - rect.bottom, rect.x, h - rect.y, rect.right)
    if degrees == 180:
        return Rect.from_edges(w - rect.right, h - rect.bottom, w - rect.x, h - rect.y)
    if degrees == 270:
        return Rect.from_edges(rect.y, w - rect.right, rect.bottom, w - rect.x)
    raise ValueError(f"Поворот кратен 90°, получено: {degrees}")


def sizes_match(a: Size, b: Size, tolerance: float, ignore_orientation: bool = True) -> bool:
    """Сравнение размеров листа с допуском (PDF любит хранить 595.276 или 595.32)."""
    pairs = [(a.width, b.width), (a.height, b.height)]
    if ignore_orientation:
        aw, ah = sorted((a.width, a.height))
        bw, bh = sorted((b.width, b.height))
        pairs = [(aw, bw), (ah, bh)]
    return all(math.isclose(x, y, abs_tol=tolerance) for x, y in pairs)
