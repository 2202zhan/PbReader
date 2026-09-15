"""
Цена. Считается только здесь и только из тарифа.

Проверяется в том числе то, чего в интерфейсе не видно, но что заметят сразу:
деньги за чистый оборот листа, бесплатный дуплекс, копейки в итоге.
"""

import pytest

from printhub.pricing import Tariff, quote

TARIFF = Tariff(price_mono=30, price_color=150, heavy_ink_from=0.3,
                heavy_extra_mono=30, heavy_extra_color=150, min_order=0)


def test_one_plain_page_costs_one_side():
    assert quote([0.04], color=False, tariff=TARIFF).amount == 30


def test_every_printed_side_is_counted():
    """Дуплекс не должен выходить бесплатным: тонер тратится на обе стороны."""
    assert quote([0.04] * 4, color=False, tariff=TARIFF).amount == 120


def test_the_price_follows_the_sides_it_is_given():
    """Сюда приходят только запечатанные стороны — чистый оборот отсеивается
    раньше, при разборе раскладки. Здесь проверяется, что счёт идёт по списку,
    а не по числу листов; что оборот в список не попадает — в test_orders.py."""
    assert quote([0.04], color=False, tariff=TARIFF).amount == 30
    assert quote([0.04, 0.04], color=False, tariff=TARIFF).amount == 60


def test_colour_is_charged_by_its_own_rate():
    assert quote([0.04, 0.04], color=True, tariff=TARIFF).amount == 300


def test_a_filled_page_costs_more():
    """Тот самый чёрный квадрат: PbReader меряет, тариф решает цену."""
    result = quote([0.9], color=False, tariff=TARIFF)
    assert result.amount == 60
    assert result.lines[-1].title == "Сильно залитые страницы"


def test_the_surcharge_applies_only_to_the_heavy_sides():
    result = quote([0.9, 0.02, 0.5], color=False, tariff=TARIFF)
    assert result.amount == 30 * 3 + 30 * 2


def test_the_threshold_is_inclusive():
    """Ровно на границе — уже тяжёлая: иначе граница зависела бы от округления."""
    assert quote([0.30], color=False, tariff=TARIFF).amount == 60
    assert quote([0.2999], color=False, tariff=TARIFF).amount == 30


def test_a_small_order_is_lifted_to_the_minimum():
    tariff = Tariff(price_mono=30, min_order=200, heavy_extra_mono=0)
    result = quote([0.04], color=False, tariff=tariff)
    assert result.amount == 200
    assert result.lines[-1].title == "Доплата до минимального заказа"
    assert result.lines[-1].amount == 170


def test_a_large_order_is_not_touched_by_the_minimum():
    tariff = Tariff(price_mono=30, min_order=200, heavy_extra_mono=0)
    result = quote([0.04] * 10, color=False, tariff=tariff)
    assert result.amount == 300
    assert all("минималь" not in line.title for line in result.lines)


def test_nothing_to_print_costs_nothing():
    assert quote([], color=False, tariff=TARIFF).amount == 0


def test_the_breakdown_adds_up_to_the_total():
    """Строки расчёта показываются человеку — они обязаны сходиться с итогом."""
    result = quote([0.9, 0.04, 0.5, 0.01], color=True, tariff=TARIFF)
    assert sum(line.amount for line in result.lines) == result.amount


@pytest.mark.parametrize("sides", [1, 2, 7, 100])
def test_the_amount_is_always_a_whole_number_of_tenge(sides):
    """Деньги целые: float дал бы 149.99999999999997 в чеке."""
    result = quote([0.1] * sides, color=True, tariff=TARIFF)
    assert isinstance(result.amount, int)
