"""Как найти адрес принтера для SNMP, зная только его имя в Windows."""

import pytest

from pbreader.printers.network import resolve_host


class TestPortNames:
    @pytest.mark.parametrize("port,expected", [
        ("IP_192.168.1.50", "192.168.1.50"),          # обычное имя от «Стандартного порта TCP/IP»
        ("192.168.1.50", "192.168.1.50"),
        ("192.168.1.50_1", "192.168.1.50"),
        ("printer.office.local", "printer.office.local"),
    ])
    def test_network_ports_give_an_address(self, port, expected):
        assert resolve_host("HP", port) == expected

    @pytest.mark.parametrize("port", ["USB001", "LPT1:", "COM3", "FILE:", "PORTPROMPT:", "nul:"])
    def test_local_ports_have_nothing_to_ask(self, port):
        """У принтера на кабеле сетевого адреса нет — и это не ошибка."""
        assert resolve_host("HP", port) is None

    def test_unrecognised_port_is_not_guessed(self, port="Standard TCP/IP Port"):
        assert resolve_host("HP", port) is None

    def test_empty_port_is_not_an_error(self):
        assert resolve_host("HP", "") is None
