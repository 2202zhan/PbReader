"""
Форматы бумаги и их коды DMPAPER_* из Windows DEVMODE.

Формат листа задаётся явно всегда. Если этого не сделать, драйвер берёт размер
из своих настроек по умолчанию, а там на новом аппарате запросто окажется
Letter — и весь документ уезжает по полям, причём молча.
"""

from __future__ import annotations

from dataclasses import dataclass

from .units import Size, mm_to_pt, sizes_match

# Допуск при опознании формата: PDF-генераторы округляют A4 то в 595.28, то в
# 595.32, то в 596 — это всё ещё A4, а не «нестандартный размер».
SIZE_TOLERANCE_PT = mm_to_pt(2.0)


@dataclass(frozen=True)
class Paper:
    name: str
    size: Size
    dmpaper: int  # код DMPAPER_* для DEVMODE.PaperSize

    @property
    def width_mm(self) -> float:
        return self.size.width / 72.0 * 25.4

    @property
    def height_mm(self) -> float:
        return self.size.height / 72.0 * 25.4


def _mm(width: float, height: float) -> Size:
    return Size(mm_to_pt(width), mm_to_pt(height))


# Книжная ориентация — каноническая: лист всегда подаётся в принтер книжным,
# а альбомность даёт поворот содержимого (см. pbreader.layout).
A4 = Paper("A4", _mm(210, 297), 9)
A3 = Paper("A3", _mm(297, 420), 8)
A5 = Paper("A5", _mm(148, 210), 11)
LETTER = Paper("Letter", _mm(215.9, 279.4), 1)
LEGAL = Paper("Legal", _mm(215.9, 355.6), 5)

PAPERS: dict[str, Paper] = {p.name.lower(): p for p in (A4, A3, A5, LETTER, LEGAL)}
DEFAULT_PAPER = A4


def get_paper(name: str | None) -> Paper:
    if not name:
        return DEFAULT_PAPER
    paper = PAPERS.get(str(name).strip().lower())
    if paper is None:
        raise ValueError(f"Неизвестный формат бумаги: {name!r}. Доступны: {', '.join(sorted(PAPERS))}")
    return paper


def identify(size: Size, tolerance_pt: float = SIZE_TOLERANCE_PT) -> Paper | None:
    """Опознаёт формат листа по размеру, не глядя на ориентацию."""
    for paper in PAPERS.values():
        if sizes_match(size, paper.size, tolerance_pt):
            return paper
    return None
