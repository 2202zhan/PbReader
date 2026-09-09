"""
Клиент SNMP и разбор Printer-MIB.

Проверяется целиком, вместе с UDP: на стыке кодирования и сети ошибки и
прячутся. Поддельный принтер отвечает на настоящем сокете.
"""

import pytest

import pbreader.snmp as snmp
from pbreader.printers import telemetry as mib
from pbreader.snmp import SnmpClient, SnmpError, SnmpTimeout, as_text, decode_oid, encode_oid
from snmp_agent import FakePrinter

HP_VALUES = {
    mib.OID_SYS_DESCR: "HP ETHERNET MULTI-ENVIRONMENT",
    mib.OID_PRINTER_NAME: "HP LaserJet M507dn",
    mib.OID_SERIAL: "PHBCD12345",
    mib.OID_MARKER_LIFE_COUNT: ("counter", 148253),
    mib.OID_DEVICE_STATUS: 2,
    mib.OID_PRINTER_STATUS: 3,
    mib.OID_ERROR_STATE: "\x00\x00",
    mib.OID_INPUT_LEVEL + ".1.1": 250,
    mib.OID_INPUT_MAX + ".1.1": 550,
    mib.OID_INPUT_NAME + ".1.1": "Лоток 2",
    mib.OID_INPUT_LEVEL + ".1.2": 0,
    mib.OID_INPUT_MAX + ".1.2": 100,
    mib.OID_INPUT_NAME + ".1.2": "Обходной лоток",
    mib.OID_SUPPLY_LEVEL + ".1.1": 1200,
    mib.OID_SUPPLY_MAX + ".1.1": 10000,
    mib.OID_SUPPLY_DESCRIPTION + ".1.1": "Black Cartridge HP 89A",
}


@pytest.fixture
def printer():
    with FakePrinter(HP_VALUES) as device:
        yield device


@pytest.fixture
def client(printer):
    return SnmpClient("127.0.0.1", port=printer.port, timeout=1.0, retries=0)


@pytest.fixture
def read(printer, monkeypatch):
    """Читает Printer-MIB с поддельного принтера."""
    original = SnmpClient.__init__

    def patched(self, host, community="public", port=snmp.SNMP_PORT, timeout=2.0, retries=2):
        original(self, host, community, printer.port, timeout, retries)

    monkeypatch.setattr(SnmpClient, "__init__", patched)
    return lambda: mib.read("127.0.0.1", timeout=1.0)


class TestOid:
    @pytest.mark.parametrize("oid", [
        "1.3.6.1.2.1.1.1.0",
        "1.3.6.1.2.1.43.10.2.1.4.1.1",
        "1.3.6.1.4.1.11.2.3.9.4.2.1.1.16.5.0",
    ])
    def test_round_trip(self, oid):
        assert decode_oid(encode_oid(oid)[2:]) == oid

    def test_large_subidentifiers_use_seven_bit_groups(self):
        """Числа больше 127 пакуются по семь бит — на этом легко ошибиться."""
        assert decode_oid(encode_oid("1.3.6.1.4.1.99999.1")[2:]) == "1.3.6.1.4.1.99999.1"

    def test_too_short_is_refused(self):
        with pytest.raises(ValueError, match="Слишком короткий"):
            encode_oid("1")


class TestClient:
    def test_get_returns_the_value(self, client):
        assert client.get_one(mib.OID_MARKER_LIFE_COUNT) == 148253

    def test_several_values_in_one_packet(self, client, printer):
        before = printer.requests
        values = client.get(mib.OID_PRINTER_NAME, mib.OID_SERIAL, mib.OID_MARKER_LIFE_COUNT)
        assert len(values) == 3
        assert printer.requests == before + 1, "три значения — один запрос"

    def test_missing_value_is_simply_absent(self, client):
        assert client.get("1.3.6.1.4.1.99999.1") == {}
        assert client.get_one("1.3.6.1.4.1.99999.1", default="нет") == "нет"

    def test_walk_reads_a_table(self, client):
        rows = list(client.walk(mib.OID_INPUT_LEVEL))
        assert [value for _, value in rows] == [250, 0]

    def test_walk_stops_at_the_end_of_the_subtree(self, client):
        """Обход обязан остановиться на границе поддерева, а не уползти дальше."""
        rows = list(client.walk(mib.OID_SUPPLY_LEVEL))
        assert len(rows) == 1

    def test_silent_device_times_out_instead_of_hanging(self, printer):
        """Киоск не имеет права зависнуть на опросе принтера."""
        printer.silent = True
        client = SnmpClient("127.0.0.1", port=printer.port, timeout=0.2, retries=1)
        with pytest.raises(SnmpTimeout):
            client.get(mib.OID_SYS_DESCR)
        assert printer.requests == 2, "повтор должен был случиться"

    def test_wrong_community_is_an_error_not_a_silent_zero(self, printer):
        client = SnmpClient("127.0.0.1", community="секрет", port=printer.port, timeout=0.5, retries=0)
        with pytest.raises(SnmpError):
            client.get(mib.OID_SYS_DESCR)

    def test_alive_is_a_cheap_yes_or_no(self, client, printer):
        assert client.alive() is True
        printer.silent = True
        assert SnmpClient("127.0.0.1", port=printer.port, timeout=0.2, retries=0).alive() is False


class TestOctetStrings:
    def test_binary_values_stay_binary(self):
        """OCTET STRING несёт и названия, и битовые маски — на проводе они
        неразличимы. Декодировать всё как текст значит незаметно портить
        двоичное: маска 0x20 после обрезки пробелов стала бы пустой."""
        assert snmp._decode_value(snmp.TAG_OCTET_STRING, b"\x20") == b"\x20"

    def test_text_is_extracted_where_it_is_expected(self):
        assert as_text(b" \xd0\x9b\xd0\xbe\xd1\x82\xd0\xbe\xd0\xba 2\x00") == "Лоток 2"

    def test_undecodable_bytes_do_not_lose_the_whole_value(self):
        assert as_text(b"Tray \xff\xfe 2")


class TestTelemetry:
    def test_reads_the_engine_page_counter(self, read):
        """То, чего нет в Windows: сколько листов механизм отпечатал за жизнь."""
        assert read().page_count == 148253

    def test_reads_model_and_serial(self, read):
        info = read()
        assert info.model == "HP LaserJet M507dn"
        assert info.serial == "PHBCD12345"

    def test_reads_paper_levels_per_tray(self, read):
        """Спросить, есть ли бумага, ДО оплаты — то, ради чего это и делалось."""
        trays = read().trays
        assert [(t.name, t.percent, t.is_empty) for t in trays] == [
            ("Лоток 2", 45.5, False), ("Обходной лоток", 0.0, True)
        ]

    def test_reads_supply_levels(self, read):
        supply = read().supplies[0]
        assert supply.name == "Black Cartridge HP 89A"
        assert supply.percent == 12.0

    def test_paper_is_present_while_any_tray_has_some(self, read):
        assert read().has_paper is True

    def test_unreachable_device_is_reported_not_raised(self, printer):
        printer.silent = True
        info = mib.read("127.0.0.1", timeout=0.2)
        assert info.reachable is False
        assert info.error
        assert info.page_count is None


class TestProblems:
    @pytest.mark.parametrize("mask,expected,blocks", [
        (b"\x00\x00", [], False),
        (b"\x20\x00", ["мало тонера"], False),
        (b"\x40\x00", ["нет бумаги"], True),
        (b"\x04\x00", ["замятие бумаги"], True),
        (b"\x08\x00", ["открыта крышка"], True),
        (b"\x00\x04", ["лоток подачи пуст"], True),
    ])
    def test_error_bits_become_words(self, printer, monkeypatch, mask, expected, blocks):
        values = dict(HP_VALUES)
        values[mib.OID_ERROR_STATE] = mask.decode("latin-1")
        printer.values = values

        original = SnmpClient.__init__
        monkeypatch.setattr(
            SnmpClient, "__init__",
            lambda self, host, community="public", port=None, timeout=2.0, retries=2:
                original(self, host, community, printer.port, timeout, retries),
        )
        info = mib.read("127.0.0.1", timeout=1.0)
        assert info.problems == expected
        assert bool(info.blocking) is blocks
        assert info.ready is not blocks

    def test_low_toner_warns_but_does_not_block(self, printer, monkeypatch):
        """Мало тонера — повод предупредить, а не отказать: напечатать ещё можно."""
        values = dict(HP_VALUES)
        values[mib.OID_ERROR_STATE] = "\x20\x00"
        printer.values = values
        original = SnmpClient.__init__
        monkeypatch.setattr(
            SnmpClient, "__init__",
            lambda self, host, community="public", port=None, timeout=2.0, retries=2:
                original(self, host, community, printer.port, timeout, retries),
        )
        assert mib.read("127.0.0.1", timeout=1.0).ready is True


class TestUnknownLevels:
    def test_unknown_level_is_not_read_as_empty(self):
        """Много аппаратов не умеют считать листы и отвечают −2. Считать такой
        лоток пустым значит навсегда заблокировать печать на них."""
        tray = mib.InputTray(index=1, name="Лоток", level=mib.LEVEL_UNKNOWN, capacity=-2, percent=None)
        assert tray.is_empty is False
        assert tray.level_known is False

    def test_zero_really_is_empty(self):
        tray = mib.InputTray(index=1, name="Лоток", level=0, capacity=500, percent=0.0)
        assert tray.is_empty is True

    def test_percentage_needs_both_numbers(self):
        assert mib._percent(250, 500) == 50.0
        assert mib._percent(-2, 500) is None
        assert mib._percent(250, -1) is None
