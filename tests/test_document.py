import pytest
from PIL import ImageChops

from pbreader.document import PdfDocument, PdfError, PdfPasswordRequired
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


def _dark_pixels(image, box) -> int:
    """Сколько тёмных пикселей в области — по гистограмме, без обхода попиксельно."""
    return sum(image.crop(box).histogram()[:128])


class TestForms:
    """Заполненные поля формы обязаны попадать на бумагу.

    Значения полей лежат в отдельном слое, и PDFium трогает его только после
    init_forms(). Без этого вызова человек приносит заполненное заявление, а из
    аппарата выходит чистый бланк — и заметить это можно только на бумаге.
    """

    def test_document_reports_that_it_has_forms(self, form_pdf):
        with PdfDocument(form_pdf) as document:
            assert document.has_forms

    def test_field_value_is_actually_drawn(self, form_pdf):
        from tests.conftest import FORM_FIELD_BOX

        with PdfDocument(form_pdf) as document:
            image = document.render(1, dpi=144).convert("L")
        assert _dark_pixels(image, FORM_FIELD_BOX) > 100, "внутри поля пусто — слой форм не отрисовался"

    def test_without_init_forms_the_field_is_empty(self, form_pdf):
        """Обратная сторона: показывает, что тест выше проверяет именно формы,
        а не просто наличие хоть какой-то краски на странице."""
        import pypdfium2 as pdfium

        from tests.conftest import FORM_FIELD_BOX

        raw = pdfium.PdfDocument(str(form_pdf))
        try:
            image = raw[0].render(scale=2, fill_color=(255, 255, 255, 255)).to_pil().convert("L")
        finally:
            raw.close()
        assert _dark_pixels(image, FORM_FIELD_BOX) == 0

    def test_ordinary_document_has_no_forms(self, a4_portrait_pdf):
        with PdfDocument(a4_portrait_pdf) as document:
            assert not document.has_forms


class TestPassword:
    def test_encrypted_document_without_password_is_reported_as_such(self, locked_pdf):
        with pytest.raises(PdfPasswordRequired):
            PdfDocument(locked_pdf)

    def test_wrong_password_is_reported_the_same_way(self, locked_pdf):
        with pytest.raises(PdfPasswordRequired):
            PdfDocument(locked_pdf, password="мимо")

    def test_correct_password_opens_the_document(self, locked_pdf):
        with PdfDocument(locked_pdf, password="секрет") as document:
            assert document.page_count == 2
