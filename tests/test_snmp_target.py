"""
Куда идти за телеметрией и что сказать, если идти некуда.

Написано после запуска на аппарате, где SNMP просто не сработал и НИЧЕГО не
написал в журнал: понять по нему, выключен ли опрос, не найден ли адрес или
принтер не ответил, было невозможно. Молчаливый пропуск — сам по себе ошибка.
"""

import logging

import pytest

import pbreader.printers as printers
from pbreader.printers.network import describe_port


@pytest.fixture(autouse=True)
def quiet():
    printers.forget_announced()
    yield
    printers.forget_announced()


class TestPortParsing:
    @pytest.mark.parametrize("port,host", [
        ("IP_192.168.1.50", "192.168.1.50"),
        ("192.168.1.50", "192.168.1.50"),
        ("192.168.1.50_1", "192.168.1.50"),
        ("printer.office.local", "printer.office.local"),
    ])
    def test_network_ports_give_an_address(self, port, host):
        assert describe_port("HP", port).host == host

    def test_usb_says_why_plainly(self):
        found = describe_port("HP", "USB001")
        assert found.host is None
        assert "кабелем" in found.reason

    def test_wsd_port_explains_what_to_do(self):
        """Windows сама находит принтеры и заводит порт WSD, а адрес в нём не
        лежит. Человеку нужно не «нет», а что с этим делать."""
        found = describe_port("HP", "WSD-a1b2c3d4")
        assert found.host is None
        assert "snmp_host" in found.reason

    def test_unknown_port_still_names_itself(self):
        found = describe_port("HP", "Какой-то порт")
        assert found.host is None
        assert found.port == "Какой-то порт"


class TestTarget:
    def test_configured_address_wins(self):
        target = printers.resolve_snmp("HP", host="10.0.0.7")
        assert target.host == "10.0.0.7"
        assert target.available

    def test_disabled_is_stated_as_such(self):
        """«Выключено» и «не нашли адрес» — разные вещи, и путать их нельзя."""
        target = printers.resolve_snmp("HP", enabled=False)
        assert not target.available
        assert "выключен" in target.reason

    def test_missing_address_carries_a_reason(self):
        target = printers.resolve_snmp("Принтер которого нет")
        assert not target.available
        assert target.reason

    def test_configured_community_beats_the_port_setting(self):
        target = printers.resolve_snmp("HP", host="10.0.0.7", community="секрет")
        assert target.community == "секрет"


class TestAnnouncing:
    def test_the_decision_reaches_the_log(self, caplog):
        with caplog.at_level(logging.INFO, logger="pbreader.printers"):
            printers.resolve_snmp("HP", host="10.0.0.7")
        assert any("10.0.0.7" in record.getMessage() for record in caplog.records)

    def test_the_reason_reaches_the_log_too(self, caplog):
        """Раньше здесь не было ни строчки — и телеметрия молча не работала."""
        with caplog.at_level(logging.INFO, logger="pbreader.printers"):
            printers.resolve_snmp("Принтер которого нет")
        assert caplog.records

    def test_it_is_said_once_not_on_every_request(self, caplog):
        with caplog.at_level(logging.INFO, logger="pbreader.printers"):
            for _ in range(5):
                printers.resolve_snmp("HP", host="10.0.0.7")
        assert len(caplog.records) == 1
