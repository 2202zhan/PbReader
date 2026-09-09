"""
Что за файл нам дали на самом деле.

Написано после того, как с аппарата пришли файлы из чужой базы и получили
«Failed to load document (PDFium: Data format error)». Причина была не в PDFium:
файлам без знакомого расширения программа САМА приписывала «.pdf», и книга .epub
приезжала под видом PDF. Сообщение об ошибке при этом не давало ни малейшего
шанса догадаться, в чём дело.
"""

import io
import zipfile

import pytest

from pbreader.formats import (
    DJVU,
    EPUB,
    IMAGE_JPEG,
    OLE,
    PDF,
    UNKNOWN,
    UnsupportedFormat,
    detect,
    ensure_printable,
)
from pbreader.sources import sanitize_filename


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def zipped(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class TestDetection:
    def test_pdf(self, tmp_path):
        assert detect(write(tmp_path, "a.bin", b"%PDF-1.7\n1 0 obj")) is PDF

    def test_pdf_with_junk_before_the_header(self, tmp_path):
        """Спецификация разрешает заголовку стоять не в самом начале, и такие
        файлы читаются повсеместно — отказывать в них незачем."""
        assert detect(write(tmp_path, "a.bin", b"\n\n   %PDF-1.4\n")) is PDF

    def test_epub_is_not_a_pdf(self, tmp_path):
        """Ровно тот случай с аппарата: книга из базы под видом PDF."""
        data = zipped({"mimetype": "application/epub+zip", "OEBPS/content.opf": "<x/>"})
        assert detect(write(tmp_path, "book.pdf", data)) is EPUB

    def test_word_document(self, tmp_path):
        data = zipped({"[Content_Types].xml": "<x/>", "word/document.xml": "<x/>"})
        assert detect(write(tmp_path, "a.bin", data)).key == "docx"

    def test_excel_and_powerpoint(self, tmp_path):
        excel = zipped({"[Content_Types].xml": "<x/>", "xl/workbook.xml": "<x/>"})
        deck = zipped({"[Content_Types].xml": "<x/>", "ppt/presentation.xml": "<x/>"})
        assert detect(write(tmp_path, "a.bin", excel)).key == "xlsx"
        assert detect(write(tmp_path, "b.bin", deck)).key == "pptx"

    def test_old_office_container(self, tmp_path):
        assert detect(write(tmp_path, "a.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)) is OLE

    def test_djvu(self, tmp_path):
        assert detect(write(tmp_path, "scan.pdf", b"AT&TFORM" + b"\0" * 64)) is DJVU

    def test_image(self, tmp_path):
        assert detect(write(tmp_path, "photo.pdf", b"\xff\xd8\xff\xe0" + b"\0" * 64)) is IMAGE_JPEG

    def test_empty_file(self, tmp_path):
        assert detect(write(tmp_path, "a.pdf", b"")) is UNKNOWN

    def test_missing_file(self, tmp_path):
        assert detect(tmp_path / "нет-такого") is UNKNOWN


class TestVerdict:
    def test_printable_formats_pass(self, tmp_path):
        assert ensure_printable(write(tmp_path, "a.pdf", b"%PDF-1.4\n")) is PDF

    def test_old_office_passes_and_the_extension_picks_the_application(self, tmp_path):
        """Внутри старого контейнера может быть Word, Excel или PowerPoint —
        что именно, решает расширение уже при конвертации."""
        assert ensure_printable(write(tmp_path, "a.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")) is OLE

    def test_the_refusal_names_the_actual_format(self, tmp_path):
        """«Data format error» не говорит человеку ничего. Отказ обязан
        объяснять, ЧТО он принёс."""
        data = zipped({"mimetype": "application/epub+zip"})
        path = write(tmp_path, "398baa78-8451-451c.pdf", data)
        with pytest.raises(UnsupportedFormat) as error:
            ensure_printable(path, "398baa78-8451-451c.pdf")
        message = str(error.value)
        assert "EPUB" in message
        assert "398baa78-8451-451c.pdf" in message
        assert "PDF, Word, Excel" in message

    def test_unrecognised_file_says_so_plainly(self, tmp_path):
        with pytest.raises(UnsupportedFormat, match="Не удалось распознать"):
            ensure_printable(write(tmp_path, "чтото.bin", b"\x01\x02\x03"), "чтото.bin")


class TestFilename:
    def test_the_real_extension_is_kept(self):
        """Раньше «.epub» превращалось в «.pdf» — программа врала про формат
        ещё до того, как кто-либо пытался файл открыть."""
        assert sanitize_filename("книга.epub") == "книга.epub"
        assert sanitize_filename("скан.djvu") == "скан.djvu"

    def test_a_name_without_extension_stays_without_one(self):
        assert sanitize_filename("398baa78-8451-451c") == "398baa78-8451-451c"

    def test_path_traversal_is_stripped(self):
        assert sanitize_filename("../../зло.exe") == "зло.exe"

    def test_a_nonsense_extension_is_dropped(self):
        assert sanitize_filename("файл.таблица") == "файл"

    def test_an_empty_name_still_yields_a_filename(self):
        assert sanitize_filename("").startswith("document_")
