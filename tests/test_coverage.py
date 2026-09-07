"""
Заполнение страницы: защита от намеренного слива тонера.

Аппарат берёт деньги за лист, а тонер тратится за краску на нём. Обычная
страница закрашена процентов на пять, сплошной чёрный лист — на сто: в двадцать
раз дороже при той же цене. Здесь проверяется, что эта разница действительно
видна ДО печати.
"""

import pytest

from conftest import FILL_BLACK, FILL_BLANK, FILL_GREY_50, FILL_HALF_BLACK, FILL_RED
from pbreader import A4, ColorMode, DeviceGeometry, PdfDocument, PrintJob, ScaleMode
from pbreader.coverage import (
    REFERENCE_COVERAGE,
    CoverageLimits,
    check_limits,
    measure_job,
)


def measure(path, job=None, device=None, **kwargs):
    with PdfDocument(path) as document:
        return measure_job(document, job or PrintJob(), device or DeviceGeometry.nominal(A4), **kwargs)


class TestKnownFills:
    def test_blank_page_costs_nothing(self, make_filled_pdf):
        result = measure(make_filled_pdf([FILL_BLANK]))
        assert result.pages[0].percent == 0.0
        assert result.total_ink_units == 0.0

    def test_solid_black_page_is_a_hundred_percent(self, make_filled_pdf):
        """Ровно тот случай, ради которого всё и считается."""
        result = measure(make_filled_pdf([FILL_BLACK]))
        assert result.pages[0].coverage == pytest.approx(1.0, abs=0.01)

    def test_half_black_page_is_about_half(self, make_filled_pdf):
        result = measure(make_filled_pdf([FILL_HALF_BLACK]))
        assert result.pages[0].coverage == pytest.approx(0.5, abs=0.02)

    def test_mid_grey_is_about_half(self, make_filled_pdf):
        """Серая заливка расходует примерно вдвое меньше сплошной чёрной —
        принтер кладёт её точками через растрирование."""
        result = measure(make_filled_pdf([FILL_GREY_50]))
        assert result.pages[0].coverage == pytest.approx(0.5, abs=0.03)

    def test_black_page_costs_twenty_ordinary_ones(self, make_filled_pdf):
        """За единицу взята страница с заполнением 5 % — по ней производители
        считают ресурс картриджа."""
        result = measure(make_filled_pdf([FILL_BLACK]))
        assert result.pages[0].ink_units == pytest.approx(1 / REFERENCE_COVERAGE, rel=0.02)


class TestColour:
    def test_black_uses_black_toner_only(self, make_filled_pdf):
        """Лазерный аппарат печатает чёрное чёрным тонером, а не смесью трёх
        цветных: иначе чёрный лист насчитал бы четырёхкратный расход."""
        result = measure(make_filled_pdf([FILL_BLACK]), PrintJob(color=ColorMode.COLOR))
        channels = result.pages[0].channels
        assert channels["k"] == pytest.approx(1.0, abs=0.01)
        assert channels["c"] + channels["m"] + channels["y"] == pytest.approx(0.0, abs=0.01)

    def test_red_uses_magenta_and_yellow(self, make_filled_pdf):
        """Красный получается из пурпурного и жёлтого — и стоит двух тонеров,
        то есть вдвое дороже чёрного листа."""
        result = measure(make_filled_pdf([FILL_RED]), PrintJob(color=ColorMode.COLOR))
        page = result.pages[0]
        assert page.channels["m"] == pytest.approx(1.0, abs=0.02)
        assert page.channels["y"] == pytest.approx(1.0, abs=0.02)
        assert page.channels["k"] == pytest.approx(0.0, abs=0.02)
        assert page.ink_units == pytest.approx(2 / REFERENCE_COVERAGE, rel=0.05)

    def test_pale_colour_can_be_expensive_despite_looking_light(self, make_filled_pdf):
        """Сплошной жёлтый выглядит светлым, а жёлтого тонера съедает целую
        страницу. Поэтому «заполнение» и «расход» — разные числа."""
        yellow = "1 1 0 rg 0 0 595.28 841.89 re f"
        result = measure(make_filled_pdf([yellow]), PrintJob(color=ColorMode.COLOR))
        page = result.pages[0]
        assert page.coverage < 0.2, "визуально светлая"
        assert page.channels["y"] == pytest.approx(1.0, abs=0.02), "а тонера — на полную страницу"

    def test_monochrome_job_has_no_channel_breakdown(self, make_filled_pdf):
        result = measure(make_filled_pdf([FILL_BLACK]))
        assert result.pages[0].channels is None


class TestWhatIsMeasured:
    def test_scaling_down_costs_less_toner(self, make_filled_pdf):
        """Считается то, что ляжет НА ЛИСТ. Та же чёрная страница, уменьшенная
        вчетверо, расходует вчетверо меньше — иначе оценка не про бумагу."""
        path = make_filled_pdf([FILL_BLACK])
        full = measure(path, PrintJob(scale=ScaleMode.ACTUAL))
        small = measure(path, PrintJob(scale=ScaleMode.CUSTOM, scale_percent=50.0))
        assert small.pages[0].coverage == pytest.approx(full.pages[0].coverage / 4, abs=0.03)

    def test_content_outside_the_printable_area_is_not_counted(self, make_filled_pdf):
        """Того, что принтер отрежет, на бумаге нет — и тонера оно не стоит."""
        path = make_filled_pdf([FILL_BLACK])
        fitted = measure(path, PrintJob(scale=ScaleMode.FIT))
        oversized = measure(path, PrintJob(scale=ScaleMode.CUSTOM, scale_percent=400.0))
        assert oversized.pages[0].coverage <= 1.0
        assert oversized.pages[0].coverage >= fitted.pages[0].coverage

    def test_copies_multiply_the_total(self, make_filled_pdf):
        path = make_filled_pdf([FILL_BLACK])
        one = measure(path, PrintJob(copies=1))
        three = measure(path, PrintJob(copies=3))
        assert three.total_ink_units == pytest.approx(one.total_ink_units * 3, rel=0.01)

    def test_page_range_limits_what_is_counted(self, make_filled_pdf):
        path = make_filled_pdf([FILL_BLACK, FILL_BLANK, FILL_BLANK, FILL_BLANK])
        everything = measure(path)
        clean_only = measure(path, PrintJob(pages="2-4"))
        assert clean_only.total_ink_units < everything.total_ink_units / 3


class TestLongDocuments:
    def test_a_long_document_is_sampled_not_fully_measured(self, make_filled_pdf):
        """У документа в сотни страниц замер каждой заставил бы человека ждать
        у аппарата. Для решения «пропускать или нет» выборки достаточно."""
        path = make_filled_pdf([FILL_BLACK] * 40)
        result = measure(path, max_pages=10)
        assert result.complete is False
        assert result.measured_pages == 10
        assert result.total_pages == 40

    def test_the_estimate_is_scaled_to_the_whole_job(self, make_filled_pdf):
        path = make_filled_pdf([FILL_BLACK] * 40)
        sampled = measure(path, max_pages=10)
        full = measure(path, max_pages=None)
        assert sampled.total_ink_units == pytest.approx(full.total_ink_units, rel=0.05)

    def test_short_documents_are_measured_completely(self, make_filled_pdf):
        result = measure(make_filled_pdf([FILL_BLACK] * 3), max_pages=10)
        assert result.complete is True
        assert result.measured_pages == 3


class TestLimits:
    def test_nothing_is_checked_until_a_limit_is_set(self, make_filled_pdf):
        """Порог, выставленный наугад, отказал бы человеку с фотографией —
        поэтому по умолчанию всё выключено."""
        result = measure(make_filled_pdf([FILL_BLACK]))
        verdict = check_limits(result, CoverageLimits())
        assert verdict.allowed
        assert verdict.violations == []

    def test_dark_page_trips_the_page_limit(self, make_filled_pdf):
        result = measure(make_filled_pdf([FILL_BLACK]))
        verdict = check_limits(result, CoverageLimits(max_page_coverage=0.6))
        assert len(verdict.violations) == 1
        assert "80 %" in verdict.violations[0] or "60 %" in verdict.violations[0]

    def test_ordinary_document_passes(self, make_filled_pdf):
        light = "0 0 0 rg 60 60 200 12 re f"
        result = measure(make_filled_pdf([light] * 4))
        verdict = check_limits(result, CoverageLimits(max_page_coverage=0.6, max_ink_units=40))
        assert verdict.violations == []

    def test_many_dark_pages_trip_the_job_budget(self, make_filled_pdf):
        result = measure(make_filled_pdf([FILL_BLACK] * 4))
        verdict = check_limits(result, CoverageLimits(max_ink_units=40))
        assert any("расходует тонера" in v for v in verdict.violations)

    def test_warning_only_by_default(self, make_filled_pdf):
        """Заполнение бывает большим и у честного документа: фотография
        закрашена почти целиком. Отказ — отдельное решение оператора."""
        result = measure(make_filled_pdf([FILL_BLACK]))
        verdict = check_limits(result, CoverageLimits(max_page_coverage=0.6, enforce=False))
        assert verdict.violations
        assert verdict.allowed is True

    def test_enforcement_blocks_the_job(self, make_filled_pdf):
        result = measure(make_filled_pdf([FILL_BLACK]))
        verdict = check_limits(result, CoverageLimits(max_page_coverage=0.6, enforce=True))
        assert verdict.allowed is False

    def test_violation_names_the_offending_pages(self, make_filled_pdf):
        result = measure(make_filled_pdf([FILL_BLANK, FILL_BLACK, FILL_BLANK]))
        verdict = check_limits(result, CoverageLimits(max_page_coverage=0.6))
        assert "страницах 2" in verdict.violations[0]

    def test_limits_come_from_the_config(self):
        from pbreader.config import Config

        limits = CoverageLimits.from_config(
            Config(max_page_coverage=0.7, max_ink_units=50, enforce_coverage_limit=True)
        )
        assert (limits.max_page_coverage, limits.max_ink_units, limits.enforce) == (0.7, 50, True)

    def test_zero_in_the_config_means_off(self):
        from pbreader.config import Config

        assert CoverageLimits.from_config(Config()).active is False


class TestPreviewAgrees:
    def test_sheet_preview_reports_the_same_coverage(self, make_filled_pdf):
        """Число под предпросмотром и число в отчёте по заданию должны совпадать:
        это одна величина, и расхождение означало бы, что считают их по-разному.
        """
        from pbreader.preview import render_sheet_side
        from pbreader.sheets import plan_sheets

        path = make_filled_pdf([FILL_HALF_BLACK])
        device = DeviceGeometry.nominal(A4)
        job = PrintJob()
        with PdfDocument(path) as document:
            sheet = plan_sheets([1], job.duplex)[0]
            preview = render_sheet_side(document, job, device, sheet, width_px=400)
            measured = measure_job(document, job, device)
        assert preview.coverage == pytest.approx(measured.pages[0].coverage, abs=0.02)
