"""
Цена заказа.

PbReader цену не знает и знать не должен: он измеряет — сколько выйдет листов,
сколько сторон запечатается, сколько тонера уйдёт. Сколько это стоит, решаем мы,
и решение лежит в тарифе, который админ меняет из панели, не трогая код.

Деньги — целые тенге. Дробей в обороте нет, а float на деньгах рано или поздно
даёт 149.99999999999997 в чеке.

Считается это всегда на сервере и только из тарифа. Цену, пришедшую от клиента,
не принимаем никогда: веб-апп — код на чужом телефоне, и «итого 1 ₸» подделать
проще, чем кажется.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Tariff:
    """Прайс. Всё в тенге за одну ЗАПЕЧАТАННУЮ сторону.

    Именно за сторону, а не за лист: двусторонняя печать расходует вдвое больше
    тонера при той же бумаге, и «за лист» сделало бы дуплекс бесплатным.
    """

    price_mono: int = 30
    price_color: int = 150

    #: С какой доли заполнения сторона считается тяжёлой (0..1). Обычная
    #: страница текста — около 0.05; залитая наполовину — 0.5.
    heavy_ink_from: float = 0.30
    #: Надбавка за такую сторону. Защищает от «распечатаю чёрный квадрат»:
    #: сам факт меряет PbReader, а во сколько это обходится — знаем мы.
    heavy_extra_mono: int = 30
    heavy_extra_color: int = 150

    #: Минимальная сумма заказа: одна страница дешевле, чем возня с ней.
    min_order: int = 0
    currency: str = "KZT"


@dataclass(frozen=True)
class Line:
    title: str
    count: int
    unit_price: int
    amount: int


@dataclass(frozen=True)
class Quote:
    amount: int
    currency: str
    lines: list[Line] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "amount": self.amount,
            "currency": self.currency,
            "lines": [
                {
                    "title": line.title,
                    "count": line.count,
                    "unit_price": line.unit_price,
                    "amount": line.amount,
                }
                for line in self.lines
            ],
        }


def quote(side_coverages: list[float], color: bool, tariff: Tariff) -> Quote:
    """Считает стоимость по сторонам, которые реально запечатаются.

    side_coverages — заполнение каждой ЗАПЕЧАТАННОЙ стороны, 0..1. Чистый оборот
    листа при дуплексе сюда не попадает: он не печатается, и брать за него
    деньги было бы просто неправдой.
    """
    sides = len(side_coverages)
    if sides == 0:
        return Quote(amount=0, currency=tariff.currency, lines=[])

    base_price = tariff.price_color if color else tariff.price_mono
    extra_price = tariff.heavy_extra_color if color else tariff.heavy_extra_mono

    lines = [
        Line(
            title="Цветная печать" if color else "Чёрно-белая печать",
            count=sides,
            unit_price=base_price,
            amount=base_price * sides,
        )
    ]

    heavy = sum(1 for coverage in side_coverages if coverage >= tariff.heavy_ink_from)
    if heavy and extra_price:
        lines.append(
            Line(
                title="Сильно залитые страницы",
                count=heavy,
                unit_price=extra_price,
                amount=extra_price * heavy,
            )
        )

    amount = sum(line.amount for line in lines)

    if tariff.min_order and amount < tariff.min_order:
        # Доплата до минимума показывается строкой, а не прячется в итог:
        # человек должен видеть, почему две страницы стоят как пять.
        lines.append(
            Line(
                title="Доплата до минимального заказа",
                count=1,
                unit_price=tariff.min_order - amount,
                amount=tariff.min_order - amount,
            )
        )
        amount = tariff.min_order

    return Quote(amount=amount, currency=tariff.currency, lines=lines)
