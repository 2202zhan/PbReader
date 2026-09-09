"""
Геометрия листа: опрос принтера, запасной вариант и шум в журнале.

Написано по следам первого запуска на аппарате: там драйвер опрашивался на
КАЖДЫЙ щелчок в интерфейсе, и каждый неудачный опрос выписывал предупреждение —
восемь одинаковых строк за сорок секунд.
"""

import logging

import pytest

from pbreader import A4, DeviceGeometry, PrintJob
from pbreader import devices


@pytest.fixture(autouse=True)
def clean_cache():
    devices.forget_cached_devices()
    yield
    devices.forget_cached_devices()


@pytest.fixture
def probe(monkeypatch):
    """Подменяет опрос драйвера: тест решает, что тот ответит."""
    calls = []

    def install(result):
        def fake_probe(printer, job):
            calls.append(printer)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(devices, "IS_WINDOWS", True)
        module = type(devices)("pbreader.output.gdi")
        module.probe_device = fake_probe
        monkeypatch.setitem(__import__("sys").modules, "pbreader.output.gdi", module)
        return calls

    return install


MEASURED = DeviceGeometry(
    paper_size=A4.size,
    printable=DeviceGeometry.nominal(A4).printable,
    dpi_x=600, dpi_y=600, is_measured=True,
)


class TestFallback:
    def test_without_a_printer_the_margins_are_nominal(self):
        """Предпросмотр обязан работать и без принтера — просто честно
        помеченный как приблизительный."""
        geometry = devices.resolve_device(PrintJob())
        assert geometry.is_measured is False

    def test_a_failing_driver_does_not_stop_the_preview(self, probe):
        probe(RuntimeError("драйвер молчит"))
        geometry = devices.resolve_device(PrintJob(printer="HP LaserJet M507"))
        assert geometry.is_measured is False


class TestCache:
    def test_the_driver_is_asked_once_for_repeated_requests(self, probe):
        """Человек щёлкает параметры десятками, а поля зависят только от
        принтера, формата и лотка — опрашивать драйвер на каждое нажатие
        значит впустую открывать контекст печати."""
        calls = probe(MEASURED)
        job = PrintJob(printer="HP LaserJet M507")
        for _ in range(5):
            devices.resolve_device(job)
        assert len(calls) == 1

    def test_changing_the_tray_asks_again(self, probe):
        """Поля зависят от лотка: у обходного они другие."""
        calls = probe(MEASURED)
        devices.resolve_device(PrintJob(printer="HP LaserJet M507", tray=257))
        devices.resolve_device(PrintJob(printer="HP LaserJet M507", tray=258))
        assert len(calls) == 2

    def test_changing_the_paper_asks_again(self, probe):
        from pbreader import A3

        calls = probe(MEASURED)
        devices.resolve_device(PrintJob(printer="HP LaserJet M507"))
        devices.resolve_device(PrintJob(printer="HP LaserJet M507", paper=A3))
        assert len(calls) == 2

    def test_unrelated_settings_do_not_ask_again(self, probe):
        from pbreader import ColorMode, Duplex

        calls = probe(MEASURED)
        devices.resolve_device(PrintJob(printer="HP LaserJet M507"))
        devices.resolve_device(PrintJob(printer="HP LaserJet M507", copies=9,
                                        color=ColorMode.COLOR, duplex=Duplex.LONG_EDGE))
        assert len(calls) == 1

    def test_the_answer_expires(self, probe, monkeypatch):
        """Смена настроек принтера должна подхватиться сама, без перезапуска."""
        calls = probe(MEASURED)
        job = PrintJob(printer="HP LaserJet M507")
        devices.resolve_device(job)
        monkeypatch.setattr(devices, "CACHE_TTL_SECONDS", -1)
        devices.resolve_device(job)
        assert len(calls) == 2

    def test_failures_are_not_cached(self, probe):
        """Принтер мог быть просто занят — повторить стоит."""
        calls = probe(RuntimeError("занят"))
        job = PrintJob(printer="HP LaserJet M507")
        devices.resolve_device(job)
        devices.resolve_device(job)
        assert len(calls) == 2


class TestLogNoise:
    def test_the_same_problem_is_reported_once(self, probe, caplog):
        """Аппарат работает месяцами. Без этого журнал за сутки превращается в
        одну повторяющуюся строку, в которой не видно ничего другого."""
        probe(RuntimeError("драйвер молчит"))
        job = PrintJob(printer="HP LaserJet M507")
        with caplog.at_level(logging.WARNING, logger="pbreader.devices"):
            for _ in range(6):
                devices.resolve_device(job)
        assert len(caplog.records) == 1

    def test_recovery_makes_the_next_failure_audible_again(self, probe, monkeypatch, caplog):
        """Починили принтер, он снова сломался — об этом надо сказать заново,
        иначе вторая поломка утонет в памяти о первой."""
        job = PrintJob(printer="HP LaserJet M507")

        probe(RuntimeError("драйвер молчит"))
        devices.resolve_device(job)

        probe(MEASURED)  # принтер вернулся — память о жалобе сбрасывается
        devices.resolve_device(job)

        monkeypatch.setattr(devices, "CACHE_TTL_SECONDS", -1)
        probe(RuntimeError("снова молчит"))
        caplog.clear()  # интересует только жалоба на ВТОРУЮ поломку
        with caplog.at_level(logging.WARNING, logger="pbreader.devices"):
            devices.resolve_device(job)
        assert len(caplog.records) == 1
