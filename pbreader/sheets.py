"""
Раскладка страниц по листам: дуплекс, копии, подбор.

Отдельный модуль, потому что это нужно ДВАЖДЫ и одинаково:

* предпросмотру — чтобы показывать именно листы («лист 2 из 5, оборот»), а не
  страницы PDF. При двусторонней печати это разные вещи, и человек у аппарата
  считает листы: он их потом заберёт из лотка и за них платит;
* печати — чтобы вставить пустую сторону там, где документ кончился на лицевой.
  Без этого при нескольких копиях с дуплексом вторая копия начинается на обороте
  последнего листа первой, и человек получает склеенные копии.
"""

from __future__ import annotations

from dataclasses import dataclass

from .job import Duplex


@dataclass(frozen=True)
class Side:
    """Одна сторона листа. page=None — сторона остаётся чистой."""

    page: int | None
    is_back: bool = False

    @property
    def is_blank(self) -> bool:
        return self.page is None


@dataclass(frozen=True)
class Sheet:
    """Физический лист бумаги."""

    number: int  # с единицы, сквозная нумерация по всему заданию
    copy: int  # номер копии, с единицы
    front: Side
    back: Side | None  # None при односторонней печати

    @property
    def sides(self) -> tuple[Side, ...]:
        return (self.front,) if self.back is None else (self.front, self.back)

    def side(self, name: str) -> Side:
        if name == "front":
            return self.front
        if name == "back":
            if self.back is None:
                raise ValueError(f"Лист {self.number} односторонний, оборота нет")
            return self.back
        raise ValueError(f"Сторона листа — 'front' или 'back', получено: {name!r}")


def plan_copy(pages: list[int], duplex: Duplex) -> list[tuple[int | None, int | None]]:
    """Разбивает страницы одной копии на пары (лицо, оборот)."""
    if not pages:
        raise ValueError("Список страниц пуст")
    if not duplex.is_duplex:
        return [(page, None) for page in pages]

    pairs: list[tuple[int | None, int | None]] = []
    for i in range(0, len(pages), 2):
        front = pages[i]
        back = pages[i + 1] if i + 1 < len(pages) else None
        pairs.append((front, back))
    return pairs


def plan_sheets(
    pages: list[int],
    duplex: Duplex,
    copies: int = 1,
    collate: bool = True,
) -> list[Sheet]:
    """Полный план задания: какие листы выйдут из аппарата и что на каждом.

    При `collate=True` копии идут целиком одна за другой (1-2-3, 1-2-3), при
    `collate=False` — каждый лист повторяется подряд (1-1, 2-2, 3-3).
    """
    if copies < 1:
        raise ValueError(f"Число копий должно быть ≥ 1, получено: {copies}")

    pairs = plan_copy(pages, duplex)
    order: list[tuple[int, tuple[int | None, int | None]]] = []
    if collate:
        for copy in range(1, copies + 1):
            order.extend((copy, pair) for pair in pairs)
    else:
        for pair in pairs:
            order.extend((copy, pair) for copy in range(1, copies + 1))

    sheets: list[Sheet] = []
    for number, (copy, (front, back)) in enumerate(order, start=1):
        sheets.append(
            Sheet(
                number=number,
                copy=copy,
                front=Side(front, is_back=False),
                back=Side(back, is_back=True) if duplex.is_duplex else None,
            )
        )
    return sheets


def emission_order(sheets: list[Sheet]) -> list[int | None]:
    """Последовательность страниц для отправки в принтер.

    None означает пустую страницу: при включённом дуплексе драйвер сам кладёт
    соседние страницы на две стороны одного листа, поэтому «пропустить оборот»
    можно только отправив пустой лист — иначе следующая копия наедет на оборот
    предыдущей.
    """
    pages: list[int | None] = []
    for sheet in sheets:
        for side in sheet.sides:
            pages.append(side.page)
    # Хвостовые пустые страницы отправлять незачем: после последнего листа
    # дуплексу уже нечего портить, а лишняя чистая страница — это лишний
    # прогон и лишний тонер.
    while pages and pages[-1] is None:
        pages.pop()
    return pages
