"""
Что за файл нам на самом деле дали.

Расширению верить нельзя, а его отсутствию — тем более: файлы приходят из чужой
базы, где имя может быть просто набором цифр. Раньше такому файлу молча
приписывалось «.pdf», и человек получал «Failed to load document (PDFium: Data
format error)» — сообщение, из которого невозможно понять, что он принёс не PDF.

Поэтому формат определяется по содержимому, а называется человеческим словом.
Отказ должен объяснять, ЧТО не так, а не то, где именно сломалось.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: Сколько байт хватает, чтобы опознать формат по сигнатуре.
HEAD_BYTES = 512


@dataclass(frozen=True)
class FileFormat:
    key: str
    #: Как называть формат человеку.
    title: str
    #: Печатаем ли мы такое.
    supported: bool = False
    extension: str = ""


PDF = FileFormat("pdf", "PDF", True, ".pdf")
DOCX = FileFormat("docx", "документ Word", True, ".docx")
XLSX = FileFormat("xlsx", "таблица Excel", True, ".xlsx")
PPTX = FileFormat("pptx", "презентация PowerPoint", True, ".pptx")
RTF = FileFormat("rtf", "документ RTF", True, ".rtf")

EPUB = FileFormat("epub", "электронная книга EPUB")
DJVU = FileFormat("djvu", "документ DjVu")
IMAGE_JPEG = FileFormat("jpeg", "изображение JPEG")
IMAGE_PNG = FileFormat("png", "изображение PNG")
ZIP = FileFormat("zip", "архив ZIP")
#: Старый контейнер Office: внутри может быть .doc, .xls или .ppt — что именно,
#: решается по расширению уже при конвертации.
OLE = FileFormat("ole", "документ Microsoft Office (старый формат)", True)
UNKNOWN = FileFormat("unknown", "неизвестный формат")

#: Сигнатуры в начале файла. Порядок важен: сначала точные, потом общие.
_SIGNATURES: tuple[tuple[bytes, FileFormat], ...] = (
    (b"%PDF", PDF),
    (b"{\\rtf", RTF),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", OLE),   # старые Word/Excel/PowerPoint
    (b"\xff\xd8\xff", IMAGE_JPEG),
    (b"\x89PNG\r\n\x1a\n", IMAGE_PNG),
    (b"AT&TFORM", DJVU),
)

#: Что лежит внутри ZIP-контейнера — по этому различаются docx/xlsx/pptx/epub.
_ZIP_MARKERS: tuple[tuple[bytes, FileFormat], ...] = (
    (b"application/epub+zip", EPUB),
    (b"word/", DOCX),
    (b"xl/", XLSX),
    (b"ppt/", PPTX),
)


def _from_zip(path: Path) -> FileFormat:
    """Различает форматы, упакованные в ZIP: docx, xlsx, pptx, epub."""
    try:
        import zipfile

        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            # У EPUB первым файлом лежит mimetype с точным типом внутри.
            if "mimetype" in names:
                try:
                    if b"epub" in archive.read("mimetype"):
                        return EPUB
                except Exception:
                    pass
            joined = "\n".join(names[:200]).encode("utf-8", "ignore")
            for marker, fmt in _ZIP_MARKERS:
                if marker in joined:
                    return fmt
    except Exception:
        return ZIP
    return ZIP


def detect(path: str | Path) -> FileFormat:
    """Определяет формат файла по содержимому, а не по имени."""
    path = Path(path)
    try:
        with open(path, "rb") as handle:
            head = handle.read(HEAD_BYTES)
    except OSError:
        return UNKNOWN

    if not head:
        return UNKNOWN

    for signature, fmt in _SIGNATURES:
        if head.startswith(signature):
            return _from_zip(path) if fmt is ZIP else fmt

    if head.startswith(b"PK\x03\x04"):
        return _from_zip(path)

    # Некоторые PDF начинаются с мусора: спецификация разрешает заголовку
    # находиться в первых килобайтах, и такие файлы читаются повсеместно.
    if b"%PDF" in head:
        return PDF

    return UNKNOWN


class UnsupportedFormat(ValueError):
    """Файл не того формата — с объяснением, какого именно."""

    def __init__(self, name: str, fmt: FileFormat) -> None:
        self.format = fmt
        if fmt is UNKNOWN:
            message = (
                f"Не удалось распознать файл «{name}». "
                f"Печатать можно PDF, Word, Excel и PowerPoint."
            )
        else:
            message = (
                f"Файл «{name}» — это {fmt.title}, такой формат печатать нельзя. "
                f"Подойдут PDF, Word, Excel и PowerPoint."
            )
        super().__init__(message)


def ensure_printable(path: str | Path, name: str = "") -> FileFormat:
    """Проверяет, что файл вообще можно печатать. Иначе — понятный отказ."""
    fmt = detect(path)
    if not fmt.supported:
        raise UnsupportedFormat(name or Path(path).name, fmt)
    return fmt
