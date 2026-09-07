"""
Главный тест библиотеки: предпросмотр обязан совпадать с тем, что уйдёт на бумагу.

Всё остальное — арифметика раскладки, планирование листов, разбор диапазонов —
служит этому. Здесь проверяется само обещание: собранные полосы печати и
картинка предпросмотра — это одно и то же изображение.
"""

import pytest
from PIL import Image, ImageChops, ImageStat

from pbreader.document import PdfDocument
from pbreader.geometry import build_sheet
from pbreader.job import ColorMode, Orientation, PrintJob, ScaleMode
from pbreader.layout import compute_placement, resolve_orientation
from pbreader.paper import A4
from pbreader.raster import MAX_BAND_BYTES, iter_print_bands, print_dpi_for, render_for_preview
from pbreader.units import PT_PER_INCH

DPI = 120


def _placement(document, job, device, page=1):
    page_size = document.page_size(page)
    sheet = build_sheet(device, resolve_orientation(page_size, job))
    return compute_placement(page_size, sheet, job)


def _paper_canvas(device, dpi):
    return Image.new(
        "RGB",
        (
            round(device.paper_size.width / PT_PER_INCH * dpi),
            round(device.paper_size.height / PT_PER_INCH * dpi),
        ),
        (255, 255, 255),
    )


def _assemble_print(document, job, device, dpi=DPI, page=1, **band_kwargs):
    """Собирает то, что уйдёт в принтер, обратно в один лист."""
    placement = _placement(document, job, device, page)
    canvas = _paper_canvas(device, dpi)
    bands = list(iter_print_bands(document, page, placement, job, dpi, **band_kwargs))
    for band in bands:
        left, top, width, height = band.dest_px
        canvas.paste(band.image.resize((width, height), Image.LANCZOS), (left, top))
    return canvas, bands, placement


def _assemble_preview(document, job, device, dpi=DPI, page=1):
    """Собирает предпросмотр и приводит его к виду физического листа."""
    placement = _placement(document, job, device, page)
    sheet = placement.sheet
    canvas = Image.new(
        "RGB",
        (
            round(sheet.size.width / PT_PER_INCH * dpi),
            round(sheet.size.height / PT_PER_INCH * dpi),
        ),
        (255, 255, 255),
    )
    raster = render_for_preview(document, page, placement, job, dpi)
    canvas.paste(raster.image, (raster.dest_px[0], raster.dest_px[1]))

    # Принтер не печатает за пределами области печати — предпросмотр показывает
    # это блёклым, а для сравнения просто убираем.
    printable = Image.new("RGB", canvas.size, (255, 255, 255))
    left, top, width, height = sheet.printable.rounded_px(dpi)
    printable.paste(canvas.crop((left, top, left + width, top + height)), (left, top))

    if sheet.emit_rotation:
        printable = printable.rotate(-sheet.emit_rotation, expand=True, fillcolor=(255, 255, 255))
    return printable


def _mean_difference(first: Image.Image, second: Image.Image) -> float:
    if first.size != second.size:
        second = second.resize(first.size, Image.LANCZOS)
    return sum(ImageStat.Stat(ImageChops.difference(first, second)).mean) / 3.0


class TestPreviewMatchesPrint:
    @pytest.mark.parametrize(
        "orientation,scale",
        [
            (Orientation.PORTRAIT, ScaleMode.AUTO),
            (Orientation.PORTRAIT, ScaleMode.FIT),
            (Orientation.LANDSCAPE, ScaleMode.AUTO),
            (Orientation.LANDSCAPE, ScaleMode.FIT),
            (Orientation.PORTRAIT, ScaleMode.FILL),
        ],
    )
    def test_assembled_print_equals_preview(self, make_pdf, device, orientation, scale):
        path = make_pdf([(A4.size.width, A4.size.height, 0)])
        job = PrintJob(orientation=orientation, scale=scale)
        with PdfDocument(path) as document:
            printed, _, _ = _assemble_print(document, job, device)
            previewed = _assemble_preview(document, job, device)
        assert _mean_difference(printed, previewed) < 2.0

    def test_holds_for_a_rotated_source_page(self, make_pdf, device):
        """Скан с /Rotate 90 — самый частый случай, на котором ломался старый путь."""
        path = make_pdf([(A4.size.width, A4.size.height, 90)])
        job = PrintJob(orientation=Orientation.LANDSCAPE)
        with PdfDocument(path) as document:
            printed, _, _ = _assemble_print(document, job, device)
            previewed = _assemble_preview(document, job, device)
        assert _mean_difference(printed, previewed) < 2.0

    def test_holds_with_asymmetric_margins(self, make_pdf, asymmetric_device):
        path = make_pdf([(A4.size.width, A4.size.height, 0)])
        job = PrintJob(orientation=Orientation.LANDSCAPE, scale=ScaleMode.FIT)
        with PdfDocument(path) as document:
            printed, _, _ = _assemble_print(document, job, asymmetric_device)
            previewed = _assemble_preview(document, job, asymmetric_device)
        assert _mean_difference(printed, previewed) < 2.0


class TestBanding:
    def test_bands_tile_the_visible_area_without_gaps_or_overlap(self, make_pdf, device):
        """Полосы должны стыковаться пиксель в пиксель.

        Щель между полосами — это белая нить поперёк листа, и заметна она именно
        там, где её меньше всего ждут: на тёмной заливке и на фотографии.
        """
        path = make_pdf([(A4.size.width, A4.size.height, 0)])
        job = PrintJob()
        with PdfDocument(path) as document:
            _, bands, placement = _assemble_print(document, job, device, max_band_bytes=64 * 1024)

        assert len(bands) > 4, "проверять стыковку имеет смысл только на многих полосах"
        expected_top = bands[0].dest_px[1]
        for band in bands:
            left, top, width, height = band.dest_px
            assert top == expected_top
            assert (left, width) == (bands[0].dest_px[0], bands[0].dest_px[2])
            expected_top = top + height

        visible_px = placement.visible.rounded_px(120)
        assert expected_top == pytest.approx(visible_px[1] + visible_px[3], abs=1)

    def test_banded_output_matches_unbanded(self, make_pdf, device):
        path = make_pdf([(A4.size.width, A4.size.height, 0)])
        job = PrintJob()
        with PdfDocument(path) as document:
            whole, _, _ = _assemble_print(document, job, device, max_band_bytes=MAX_BAND_BYTES)
            sliced, bands, _ = _assemble_print(document, job, device, max_band_bytes=64 * 1024)
        assert len(bands) > 1
        assert _mean_difference(whole, sliced) < 2.0

    def test_nothing_is_rasterised_outside_the_printable_area(self, make_pdf, device):
        """Невидимую часть не растеризуем: у большого документа она может быть
        больше видимой, а принтер её всё равно отрежет."""
        path = make_pdf([(A4.size.width * 2, A4.size.height * 2, 0)])
        job = PrintJob(scale=ScaleMode.ACTUAL)
        with PdfDocument(path) as document:
            _, bands, placement = _assemble_print(document, job, device)

        printable_px = placement.sheet.device.printable.rounded_px(DPI)
        for band in bands:
            left, top, width, height = band.dest_px
            assert left >= printable_px[0] - 1
            assert top >= printable_px[1] - 1
            assert left + width <= printable_px[0] + printable_px[2] + 1
            assert top + height <= printable_px[1] + printable_px[3] + 1


class TestPrintDpi:
    def test_monochrome_gets_full_resolution(self):
        assert print_dpi_for(PrintJob(color=ColorMode.MONOCHROME)) == 600

    def test_colour_is_capped_lower_to_keep_memory_sane(self):
        assert print_dpi_for(PrintJob(color=ColorMode.COLOR)) == 300

    def test_never_exceeds_what_the_printer_can_do(self):
        assert print_dpi_for(PrintJob(print_dpi=1200), device_dpi=600) == 600

    def test_explicit_setting_wins_below_the_cap(self):
        assert print_dpi_for(PrintJob(print_dpi=200), device_dpi=600) == 200
