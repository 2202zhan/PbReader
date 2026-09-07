import pytest

from pbreader.geometry import DeviceGeometry, build_sheet
from pbreader.job import Orientation
from pbreader.paper import A4, identify
from pbreader.units import Rect, Size, mm_to_pt, rotate_rect


class TestRotation:
    def test_rectangle_survives_a_full_turn(self):
        space = Size(200, 300)
        rect = Rect(10, 20, 30, 40)
        turned = rect
        for _ in range(4):
            turned = rotate_rect(turned, 90, space if _ % 2 == 0 else space.swapped())
        assert (turned.x, turned.y, turned.width, turned.height) == pytest.approx(
            (rect.x, rect.y, rect.width, rect.height)
        )

    def test_only_right_angles_are_allowed(self):
        with pytest.raises(ValueError, match="кратен 90"):
            rotate_rect(Rect(0, 0, 1, 1), 45, Size(10, 10))


class TestSheet:
    def test_portrait_sheet_is_the_paper_itself(self, device):
        sheet = build_sheet(device, Orientation.PORTRAIT)
        assert sheet.size == device.paper_size
        assert sheet.emit_rotation == 0

    def test_landscape_sheet_swaps_the_sides(self, device):
        sheet = build_sheet(device, Orientation.LANDSCAPE)
        assert sheet.size == device.paper_size.swapped()
        assert sheet.emit_rotation == 90

    def test_auto_must_be_resolved_before_this_point(self, device):
        with pytest.raises(ValueError, match="AUTO"):
            build_sheet(device, Orientation.AUTO)

    def test_asymmetric_margins_land_where_they_belong(self, asymmetric_device):
        """Поле, которое на бумаге снизу, на альбомном листе должно оказаться
        справа — иначе предпросмотр покажет поля не с той стороны."""
        sheet = build_sheet(asymmetric_device, Orientation.LANDSCAPE)
        left, top, right, bottom = sheet.margins_mm()
        assert right == pytest.approx(10.0, abs=0.1)  # было снизу
        assert left == pytest.approx(3.0, abs=0.1)  # было сверху

    def test_margins_are_reported_in_millimetres(self, device):
        sheet = build_sheet(device, Orientation.PORTRAIT)
        assert all(m == pytest.approx(4.2, abs=0.05) for m in sheet.margins_mm())


class TestPaper:
    def test_a4_is_recognised_regardless_of_orientation(self):
        assert identify(A4.size) is A4
        assert identify(A4.size.swapped()) is A4

    def test_rounding_in_the_pdf_does_not_break_recognition(self):
        assert identify(Size(mm_to_pt(210) + 2, mm_to_pt(297) - 2)) is A4

    def test_unknown_format_is_not_guessed(self):
        assert identify(Size(100, 100)) is None


class TestNominalFallback:
    def test_nominal_geometry_is_flagged_as_estimated(self):
        device = DeviceGeometry.nominal(A4)
        assert device.is_measured is False
        assert device.printable.width < device.paper_size.width
