"""
Проверка перед оплатой: готов ли аппарат, есть ли бумага и тонер.

Спросить это до того, как аппарат взял деньги, дешевле, чем объясняться после.
"""

import pytest

import pbreader.printers as printers
from pbreader.printers.telemetry import InputTray, Supply, Telemetry


def telemetry(**kwargs):
    base = dict(host="192.168.1.50", reachable=True, model="HP LaserJet M507dn", page_count=1000)
    base.update(kwargs)
    return Telemetry(**base)


@pytest.fixture
def device(monkeypatch):
    def install(info):
        monkeypatch.setattr(printers, "read_telemetry", lambda *a, **k: info)
        monkeypatch.setattr(printers, "IS_WINDOWS", False)
    return install


class TestBlocking:
    def test_a_healthy_printer_passes(self, device):
        device(telemetry(trays=[InputTray(1, "Лоток 2", 250, 550, 45.5)]))
        result = printers.preflight("HP")
        assert result.ready is True
        assert result.blocking == []

    def test_a_jam_stops_the_sale(self, device):
        device(telemetry(problems=["замятие бумаги"], blocking=["замятие бумаги"]))
        result = printers.preflight("HP")
        assert result.ready is False
        assert "замятие бумаги" in result.blocking

    def test_an_empty_chosen_tray_stops_the_sale(self, device):
        """Человек выбрал лоток, а бумаги в нём нет — узнать надо сейчас."""
        device(telemetry(trays=[
            InputTray(1, "Лоток 2", 250, 550, 45.5),
            InputTray(2, "Обходной", 0, 100, 0.0),
        ]))
        assert printers.preflight("HP", tray=2).ready is False
        assert printers.preflight("HP", tray=1).ready is True

    def test_all_trays_empty_stops_the_sale(self, device):
        device(telemetry(trays=[InputTray(1, "Лоток 2", 0, 550, 0.0)]))
        result = printers.preflight("HP")
        assert result.ready is False
        assert "нет бумаги" in result.blocking[0]


class TestWarnings:
    def test_low_toner_warns_but_lets_you_print(self, device):
        device(telemetry(supplies=[Supply(1, "Чёрный картридж", 400, 10000, 4.0)]))
        result = printers.preflight("HP")
        assert result.ready is True
        assert any("4 %" in w for w in result.warnings)

    def test_a_full_cartridge_says_nothing(self, device):
        device(telemetry(supplies=[Supply(1, "Чёрный картридж", 9000, 10000, 90.0)]))
        assert printers.preflight("HP").warnings == []


class TestUnknownState:
    def test_a_silent_printer_warns_but_does_not_block(self, device):
        """SNMP могли просто выключить в настройках аппарата, а печатает он
        прекрасно. «Не знаю» — не повод отказать человеку."""
        device(Telemetry(host="", reachable=False, error="не отвечает"))
        result = printers.preflight("HP")
        assert result.ready is True
        assert result.warnings

    def test_a_tray_with_an_unknown_level_is_not_treated_as_empty(self, device):
        """Многие аппараты не считают листы. Считать такой лоток пустым значит
        навсегда заблокировать на них печать."""
        from pbreader.printers.telemetry import LEVEL_UNKNOWN

        device(telemetry(trays=[InputTray(1, "Лоток", LEVEL_UNKNOWN, -2, None)]))
        assert printers.preflight("HP").ready is True
