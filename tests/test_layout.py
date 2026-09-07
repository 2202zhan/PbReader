"""
Тесты ядра раскладки — того самого модуля, по которому одновременно рисуется
предпросмотр и печатается лист. Принтер и PDF здесь не нужны: на входе размеры,
на выходе арифметика.
"""

import pytest

from pbreader.geometry import build_sheet
from pbreader.job import Orientation, PrintJob, ScaleMode
from pbreader.layout import compute_placement, resolve_orientation
from pbreader.paper import A4
from pbreader.units import Size, mm_to_pt

A4_PORTRAIT = A4.size
A4_LANDSCAPE = A4.size.swapped()
A5_PORTRAIT = Size(mm_to_pt(148), mm_to_pt(210))


def place(page_size, device, **job_kwargs):
    job = PrintJob(**job_kwargs)
    sheet = build_sheet(device, resolve_orientation(page_size, job))
    return compute_placement(page_size, sheet, job)


class TestAutoScale:
    def test_matching_page_prints_one_to_one(self, device):
        placement = place(A4_PORTRAIT, device, orientation=Orientation.PORTRAIT)
        assert placement.resolved_scale_mode is ScaleMode.ACTUAL
        assert placement.scale == pytest.approx(1.0)

    def test_landscape_page_on_portrait_sheet_is_fitted(self, device):
        # Это тот случай, ради которого правило вообще существует: альбомная
        # страница в книжный лист по ширине не влезает, и печать «как есть»
        # срезала бы треть документа.
        placement = place(A4_LANDSCAPE, device, orientation=Orientation.PORTRAIT)
        assert placement.resolved_scale_mode is ScaleMode.FIT
        assert placement.scale < 1.0
        assert not placement.is_clipped

    def test_landscape_page_on_landscape_sheet_prints_one_to_one(self, device):
        placement = place(A4_LANDSCAPE, device, orientation=Orientation.LANDSCAPE)
        assert placement.resolved_scale_mode is ScaleMode.ACTUAL
        assert placement.scale == pytest.approx(1.0)

    def test_smaller_format_is_scaled_up_to_the_sheet(self, device):
        placement = place(A5_PORTRAIT, device, orientation=Orientation.PORTRAIT)
        assert placement.resolved_scale_mode is ScaleMode.FIT
        assert placement.scale > 1.0

    def test_tolerance_absorbs_pdf_rounding(self, device):
        # Генераторы PDF округляют A4 кто во что горазд — это всё ещё A4.
        almost_a4 = Size(A4_PORTRAIT.width + 1.5, A4_PORTRAIT.height - 1.5)
        placement = place(almost_a4, device, orientation=Orientation.PORTRAIT)
        assert placement.resolved_scale_mode is ScaleMode.ACTUAL


class TestScaleModes:
    def test_fit_never_clips(self, asymmetric_device):
        placement = place(A4_PORTRAIT, asymmetric_device, orientation=Orientation.PORTRAIT, scale=ScaleMode.FIT)
        assert not placement.is_clipped
        assert placement.clipped_fraction == 0.0

    def test_shrink_leaves_small_pages_alone(self, device):
        placement = place(A5_PORTRAIT, device, orientation=Orientation.PORTRAIT, scale=ScaleMode.SHRINK)
        assert placement.scale == pytest.approx(1.0)

    def test_shrink_still_reduces_oversized_pages(self, device):
        a3 = Size(mm_to_pt(297), mm_to_pt(420))
        placement = place(a3, device, orientation=Orientation.PORTRAIT, scale=ScaleMode.SHRINK)
        assert placement.scale < 1.0

    def test_fill_covers_the_printable_area_and_clips(self, device):
        placement = place(A4_LANDSCAPE, device, orientation=Orientation.PORTRAIT, scale=ScaleMode.FILL)
        printable = placement.sheet.printable
        assert placement.dest.width >= printable.width - 0.5
        assert placement.dest.height >= printable.height - 0.5
        assert placement.is_clipped

    def test_custom_percent_is_applied_literally(self, device):
        placement = place(
            A4_PORTRAIT, device, orientation=Orientation.PORTRAIT,
            scale=ScaleMode.CUSTOM, scale_percent=50.0,
        )
        assert placement.scale == pytest.approx(0.5)
        assert placement.dest.width == pytest.approx(A4_PORTRAIT.width / 2)


class TestCentering:
    def test_fitted_page_is_centred_in_the_printable_area(self, asymmetric_device):
        """При несимметричных полях вписанная страница должна стоять по центру
        ОБЛАСТИ ПЕЧАТИ — иначе она вылезет за край с узкой стороны."""
        placement = place(A4_PORTRAIT, asymmetric_device, orientation=Orientation.PORTRAIT, scale=ScaleMode.FIT)
        printable = placement.sheet.printable
        left_gap = placement.dest.x - printable.x
        right_gap = printable.right - placement.dest.right
        assert left_gap == pytest.approx(right_gap, abs=0.01)

    def test_unscaled_page_is_centred_on_the_paper(self, asymmetric_device):
        """А напечатанная один к одному — по центру ЛИСТА: человек, выбравший
        100 %, ждёт страницу посреди бумаги."""
        placement = place(A4_PORTRAIT, asymmetric_device, orientation=Orientation.PORTRAIT, scale=ScaleMode.ACTUAL)
        sheet = placement.sheet.size
        assert placement.dest.x == pytest.approx((sheet.width - placement.dest.width) / 2, abs=0.01)


class TestOrientation:
    def test_landscape_rotates_the_raster_not_the_driver(self, device):
        """Альбомность даёт поворот растра, а лист в принтер всегда идёт книжным.

        Ровно из-за этого Canon UFR II перестаёт быть особым случаем: флага
        ориентации, который он трактует по-своему, драйверу не передаётся.
        """
        placement = place(A4_LANDSCAPE, device, orientation=Orientation.LANDSCAPE)
        assert placement.sheet.emit_rotation == 90
        assert placement.total_rotation == 90
        paper = placement.to_paper()
        assert paper.width == pytest.approx(A4_PORTRAIT.width, abs=0.01)
        assert paper.height == pytest.approx(A4_PORTRAIT.height, abs=0.01)

    def test_printable_area_survives_the_trip_to_paper_and_back(self, asymmetric_device):
        """Несимметричные поля должны оказаться на бумаге там же, где были."""
        sheet = build_sheet(asymmetric_device, Orientation.LANDSCAPE)
        restored = sheet.to_paper(sheet.printable)
        original = asymmetric_device.printable
        assert restored.x == pytest.approx(original.x, abs=0.01)
        assert restored.y == pytest.approx(original.y, abs=0.01)
        assert restored.width == pytest.approx(original.width, abs=0.01)
        assert restored.height == pytest.approx(original.height, abs=0.01)

    def test_auto_orientation_follows_the_page(self, device):
        job = PrintJob(orientation=Orientation.AUTO)
        assert resolve_orientation(A4_LANDSCAPE, job) is Orientation.LANDSCAPE
        assert resolve_orientation(A4_PORTRAIT, job) is Orientation.PORTRAIT


class TestAutoRotate:
    def test_rotates_when_it_makes_the_page_bigger(self, device):
        placement = place(A4_LANDSCAPE, device, orientation=Orientation.PORTRAIT, auto_rotate=True, scale=ScaleMode.FIT)
        assert placement.content_rotation == 90
        straight = place(A4_LANDSCAPE, device, orientation=Orientation.PORTRAIT, scale=ScaleMode.FIT)
        assert placement.scale > straight.scale

    def test_leaves_a_matching_page_alone(self, device):
        placement = place(A4_PORTRAIT, device, orientation=Orientation.PORTRAIT, auto_rotate=True, scale=ScaleMode.FIT)
        assert placement.content_rotation == 0

    def test_is_off_by_default(self, device):
        placement = place(A4_LANDSCAPE, device, orientation=Orientation.PORTRAIT, scale=ScaleMode.FIT)
        assert placement.content_rotation == 0


class TestClipping:
    def test_one_to_one_a4_loses_only_the_unprintable_border(self, device):
        """Лист A4, напечатанный 1:1, всегда выходит на непечатаемые поля.

        Геометрически это обрезка — и предпросмотр обязан её показать. Но
        ругаться на неё нельзя: там, как правило, ничего нет. Различие между
        «вылезает» и «теряется изображение» делает pbreader.preview.
        """
        placement = place(A4_PORTRAIT, device, orientation=Orientation.PORTRAIT, scale=ScaleMode.ACTUAL)
        assert placement.is_clipped
        assert placement.clipped_fraction < 0.1

    def test_oversized_page_loses_a_lot(self, device):
        a3 = Size(mm_to_pt(297), mm_to_pt(420))
        placement = place(a3, device, orientation=Orientation.PORTRAIT, scale=ScaleMode.ACTUAL)
        assert placement.clipped_fraction > 0.4
