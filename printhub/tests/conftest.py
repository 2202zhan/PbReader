import io
import zipfile

import pypdfium2 as pdfium
import pytest


@pytest.fixture
def make_pdf(tmp_path):
    def build(pages: int = 1, name: str = "документ.pdf"):
        document = pdfium.PdfDocument.new()
        for _ in range(pages):
            document.new_page(595, 842)
        path = tmp_path / name
        document.save(str(path))
        return path

    return build


@pytest.fixture
def make_epub(tmp_path):
    """Книга, притворяющаяся PDF, — файл из чужой базы с чужим расширением."""

    def build(name: str = "книга.pdf"):
        path = tmp_path / name
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
        path.write_bytes(buffer.getvalue())
        return path

    return build
