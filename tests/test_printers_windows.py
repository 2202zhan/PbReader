"""
Разбор ответов драйвера принтера.

Эти проверки появились после первого запуска на живом аппарате: печать упала с
`invalid literal for int() with base 10: 'xdpi'`. DC_ENUMRESOLUTIONS отдаёт
список СЛОВАРЕЙ, а код распаковывал его как пары чисел. Ошибка доехала до
киоска ровно потому, что вся эта ветка была без тестов — win32 на Linux нет.
Теперь есть заглушки, и она проверяется.
"""

import pytest

import win32_stubs
from win32_stubs import DC_BINS, DC_COPIES, DC_DUPLEX, DC_ENUMRESOLUTIONS, FakeSpooler


@pytest.fixture
def windows(monkeypatch):
    """Модуль windows.py поверх поддельного спулера с типовыми ответами.

    Возвращает функцию: она ставит заглушки и отдаёт (модуль, спулер) — спулер
    нужен, чтобы проверять, что именно код у драйвера спросил.
    """
    def setup(spooler=None):
        spooler = spooler or FakeSpooler()
        return win32_stubs.install(monkeypatch, spooler), spooler

    return lambda spooler=None: setup(spooler)[0]


@pytest.fixture
def windows_pair(monkeypatch):
    def setup(spooler=None):
        spooler = spooler or FakeSpooler()
        return win32_stubs.install(monkeypatch, spooler), spooler

    return setup


class TestResolutions:
    def test_pywin32_returns_dictionaries_not_pairs(self, windows):
        """Та самая форма ответа, на которой всё сломалось на аппарате."""
        module = windows()
        assert module._parse_resolutions([{"xdpi": 600, "ydpi": 600}, {"xdpi": 1200, "ydpi": 1200}]) == [
            (600, 600), (1200, 1200)
        ]

    def test_pairs_are_also_accepted(self, windows):
        """Документация Win32 описывает пары — принимаем и их, чтобы не
        зависеть от версии обвязки."""
        assert windows()._parse_resolutions([(600, 600), (300, 300)]) == [(600, 600), (300, 300)]

    def test_flat_list_is_accepted(self, windows):
        assert windows()._parse_resolutions([600, 1200]) == [(600, 600), (1200, 1200)]

    def test_empty_answer_is_not_an_error(self, windows):
        module = windows()
        assert module._parse_resolutions(None) == []
        assert module._parse_resolutions(()) == []

    def test_unknown_shape_is_skipped_quietly(self, windows):
        assert windows()._parse_resolutions(["ерунда", {"нет": "полей"}]) == []


class TestTrays:
    def test_identifiers_are_paired_with_names(self, windows):
        trays = windows()._parse_trays([1, 257], ["Автовыбор", "Кассета 1"], "HP")
        assert [(t.id, t.name) for t in trays] == [(1, "Автовыбор"), (257, "Кассета 1")]

    def test_missing_names_fall_back_to_the_number(self, windows):
        """Лоток без названия печатать не мешает — потерянный лоток мешает."""
        trays = windows()._parse_trays([1, 257], ["Автовыбор"], "HP")
        assert trays[1].name == "Лоток 257"

    def test_extra_names_do_not_invent_trays(self, windows):
        trays = windows()._parse_trays([1], ["Автовыбор", "Кассета 1", "Кассета 2"], "HP")
        assert len(trays) == 1

    def test_unusable_identifier_is_skipped_not_fatal(self, windows):
        trays = windows()._parse_trays([1, "мусор", 258], [], "HP")
        assert [t.id for t in trays] == [1, 258]


class TestDescribe:
    def test_reads_a_realistic_printer(self, windows):
        capabilities = windows().describe("HP LaserJet M507")
        assert [t.id for t in capabilities.trays] == [1, 257, 258]
        assert capabilities.resolutions == [(600, 600), (1200, 1200)]
        assert capabilities.max_dpi == 1200
        assert capabilities.supports_duplex is True
        assert capabilities.supports_color is False
        assert capabilities.max_copies == 999

    def test_one_broken_answer_does_not_break_the_rest(self, windows):
        """Главный урок этой ошибки: возможности принтера — сведения
        СПРАВОЧНЫЕ, а спрашиваются по пути к печати. Непонятый список
        разрешений не имеет права остановить задание."""
        broken = FakeSpooler.realistic()
        broken[DC_ENUMRESOLUTIONS] = ["совершенно неожиданное"]
        capabilities = windows(FakeSpooler(capabilities=broken)).describe("HP LaserJet M507")
        assert capabilities.resolutions == []
        assert [t.id for t in capabilities.trays] == [1, 257, 258], "лотки должны уцелеть"
        assert capabilities.supports_duplex is True

    def test_a_driver_that_raises_does_not_break_printing(self, windows):
        angry = FakeSpooler.realistic()
        angry[DC_ENUMRESOLUTIONS] = ValueError("invalid literal for int() with base 10: 'xdpi'")
        angry[DC_BINS] = RuntimeError("драйвер молчит")
        capabilities = windows(FakeSpooler(capabilities=angry)).describe("HP LaserJet M507")
        assert capabilities.trays == []
        assert capabilities.resolutions == []
        assert capabilities.supports_duplex is True

    def test_silent_driver_yields_safe_defaults(self, windows):
        """Драйвер, не отвечающий ни на один запрос, — это принтер без
        известных возможностей, а не отказ печатать."""
        capabilities = windows(FakeSpooler(capabilities={})).describe("HP LaserJet M507")
        assert capabilities.trays == []
        assert capabilities.max_copies == 1
        assert capabilities.supports_duplex is False

    def test_unknown_printer_is_named_clearly(self, windows):
        from pbreader.printers import PrinterUnavailable

        with pytest.raises(PrinterUnavailable, match="Доступны"):
            windows().describe("Какой-то другой принтер")


class TestDevMode:
    def test_settings_are_marked_in_the_fields_mask(self, windows):
        """Драйвер читает только те поля, чей бит взведён в Fields. Присвоить
        значение и не тронуть маску — это и есть «драйвер игнорирует настройку»."""
        from pbreader.job import ColorMode, Duplex, PrintJob

        module = windows()
        import win32con

        devmode, _ = module.build_devmode(
            "HP LaserJet M507",
            PrintJob(printer="HP LaserJet M507", duplex=Duplex.LONG_EDGE,
                     color=ColorMode.MONOCHROME, copies=2, tray=257),
        )
        for flag in (win32con.DM_ORIENTATION, win32con.DM_PAPERSIZE, win32con.DM_COLOR,
                     win32con.DM_DUPLEX, win32con.DM_COPIES, win32con.DM_DEFAULTSOURCE):
            assert devmode.Fields & flag, f"бит {flag:#x} не взведён"

    def test_sheet_always_goes_in_portrait(self, windows):
        """Альбомность даёт поворот растра. Из-за флага ориентации в DEVMODE
        Canon UFR II и был особым случаем — драйверу его не передаём."""
        from pbreader.job import Orientation, PrintJob

        module = windows()
        import win32con

        devmode, _ = module.build_devmode(
            "HP LaserJet M507",
            PrintJob(printer="HP LaserJet M507", orientation=Orientation.LANDSCAPE),
        )
        assert devmode.Orientation == win32con.DMORIENT_PORTRAIT

    def test_unknown_tray_is_refused_with_the_list_of_real_ones(self, windows):
        from pbreader.job import PrintJob

        module = windows()
        with pytest.raises(ValueError, match="Кассета 1"):
            module.build_devmode("HP LaserJet M507", PrintJob(printer="HP LaserJet M507", tray=999))

    def test_duplex_falls_back_when_the_printer_cannot_do_it(self, windows):
        from pbreader.job import Duplex, PrintJob

        simplex_only = FakeSpooler.realistic()
        simplex_only[DC_DUPLEX] = 0
        module = windows(FakeSpooler(capabilities=simplex_only))
        import win32con

        devmode, _ = module.build_devmode(
            "HP LaserJet M507", PrintJob(printer="HP LaserJet M507", duplex=Duplex.LONG_EDGE)
        )
        assert devmode.Duplex == win32con.DMDUP_SIMPLEX

    def test_copies_go_to_the_driver_only_if_it_can_do_them_all(self, windows):
        """Делить копии между драйвером и циклом нельзя: при любом остатке от
        деления получится неверное их число."""
        from pbreader.job import PrintJob

        few = FakeSpooler.realistic()
        few[DC_COPIES] = 1
        module = windows(FakeSpooler(capabilities=few))
        _, driver_copies = module.build_devmode(
            "HP LaserJet M507", PrintJob(printer="HP LaserJet M507", copies=5)
        )
        assert driver_copies == 1

    def test_driver_is_asked_to_validate_the_devmode(self, windows_pair):
        """Драйвер должен привести DEVMODE в согласованный вид до печати:
        он поправит несовместимые сочетания, пока это ещё ничего не стоит."""
        from pbreader.job import PrintJob

        module, spooler = windows_pair()
        module.build_devmode("HP LaserJet M507", PrintJob(printer="HP LaserJet M507"))
        assert spooler.document_properties_called
