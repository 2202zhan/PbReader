import pytest
from PIL import ImageChops

from pbreader.document import PdfDocument, PdfError
from pbreader.paper import A4


class TestPageGeometry:
    def test_rotated_page_is_reported_as_the_reader_sees_it(self, make_pdf):
        """Страница с /Rotate 90 — альбомная, как её ни храни в файле.

        Сканы и снимки с телефона приходят именно такими. Прежняя реализация
        читала /MediaBox напрямую, считала такую страницу книжной и отправляла
        документ на печать боком.
        """
        path = make_pdf([(A4.size.width, A4.size.height, 90)])
        with PdfDocument(path) as document:
            info = document.page_info(1)
            assert info.rotation == 90
            assert info.size.is_landscape
            assert info.size.width == pytest.approx(A4.size.height, abs=1.0)

    def test_unrotated_page_keeps_its_size(self, a4_portrait_pdf):
        with PdfDocument(a4_portrait_pdf) as document:
            assert document.page_count == 3
            assert not document.page_size(1).is_landscape

    def test_missing_page_is_reported_clearly(self, a4_portrait_pdf):
        with PdfDocument(a4_portrait_pdf) as document:
            with pytest.raises(IndexError, match="в документе 3"):
                document.page_size(9)

    def test_broken_file_is_reported_clearly(self, tmp_path):
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"not a pdf at all")
        with pytest.raises(PdfError, match="Не удалось открыть"):
            PdfDocument(broken)


class TestRendering:
    def test_render_size_follows_dpi(self, a4_portrait_pdf):
        with PdfDocument(a4_portrait_pdf) as document:
            image = document.render(1, dpi=72)
            assert image.size[0] == pytest.approx(A4.size.width, abs=2)

    def test_rotation_swaps_the_raster(self, a4_portrait_pdf):
        with PdfDocument(a4_portrait_pdf) as document:
            straight = document.render(1, dpi=36)
            turned = document.render(1, dpi=36, rotation=90)
            assert turned.size == (straight.size[1], straight.size[0])

    def test_grayscale_removes_colour(self, a4_portrait_pdf):
        """Ч/б предпросмотр должен показывать документ таким, каким его напечатает
        чёрно-белый аппарат, — вместе с тем, во что превратятся цветные заливки."""
        with PdfDocument(a4_portrait_pdf) as document:
            image = document.render(1, dpi=36, grayscale=True)
            red, green, blue = image.split()
            assert ImageChops.difference(red, green).getbbox() is None
            assert ImageChops.difference(green, blue).getbbox() is None
