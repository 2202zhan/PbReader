"""
Чтение и растеризация PDF (PDFium через pypdfium2).

Почему PDFium, а не MuPDF: MuPDF распространяется под AGPL, и для коммерческих
аппаратов это означает либо раскрытие своего кода, либо покупку лицензии у
Artifex. PDFium — BSD, тот же движок, которым печатает Chrome, и он же стоит за
предпросмотром печати в браузере. То есть мы берём ровно тот растеризатор,
поведению которого люди привыкли доверять.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .units import PT_PER_INCH, Size

try:  # pragma: no cover - зависит от окружения
    import pypdfium2 as pdfium
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Не установлен pypdfium2. Установите: pip install pypdfium2"
    ) from exc

from PIL import Image


class PdfError(RuntimeError):
    """Документ не открывается или повреждён."""


class PdfPasswordRequired(PdfError):
    """Документ зашифрован, пароль не подошёл или не задан."""


@dataclass(frozen=True)
class PageInfo:
    number: int  # с единицы
    size: Size  # видимый размер в пойнтах, с учётом /Rotate
    rotation: int  # собственный /Rotate страницы, градусы


class PdfDocument:
    """Открытый PDF. Потокобезопасен: PDFium не любит параллельных вызовов.

    Локальный HTTP-сервис предпросмотра отвечает в несколько потоков (браузер
    тянет соседние листы разом), а один и тот же документ при этом общий — без
    блокировки PDFium падает не воспроизводимо.
    """

    def __init__(self, path: str | Path, password: str | None = None) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        try:
            self._doc = pdfium.PdfDocument(str(self.path), password=password, autoclose=True)
            self._page_count = len(self._doc)
        except pdfium.PdfiumError as exc:
            message = str(exc).lower()
            if "password" in message:
                raise PdfPasswordRequired(f"PDF защищён паролем: {self.path.name}") from exc
            raise PdfError(f"Не удалось открыть PDF {self.path.name}: {exc}") from exc

    def __enter__(self) -> "PdfDocument":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            doc, self._doc = getattr(self, "_doc", None), None
            if doc is not None:
                doc.close()

    @property
    def page_count(self) -> int:
        return self._page_count

    def _page(self, number: int):
        if not 1 <= number <= self._page_count:
            raise IndexError(f"Страницы {number} нет: в документе {self._page_count} стр.")
        if self._doc is None:
            raise PdfError("Документ уже закрыт")
        return self._doc[number - 1]

    def page_info(self, number: int) -> PageInfo:
        """Сведения о странице.

        `size` — ВИДИМЫЙ размер: PDFium уже применил /Rotate, как это делает
        любой просмотрщик. Прежняя реализация читала /MediaBox напрямую и на
        сканах с /Rotate 90 считала альбомную страницу книжной — документ уходил
        на печать боком.
        """
        with self._lock:
            page = self._page(number)
            width, height = page.get_size()
            rotation = page.get_rotation()
        return PageInfo(number=number, size=Size(float(width), float(height)), rotation=int(rotation))

    def page_size(self, number: int) -> Size:
        return self.page_info(number).size

    def pages(self) -> Iterator[PageInfo]:
        for number in range(1, self._page_count + 1):
            yield self.page_info(number)

    def render(
        self,
        number: int,
        *,
        dpi: float,
        rotation: int = 0,
        crop_pt: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
        grayscale: bool = False,
    ) -> Image.Image:
        """Растеризует страницу (или её часть) в RGB-картинку.

        `dpi` — плотность результата. `rotation` — дополнительный поворот по
        часовой (0/90/180/270), применяется ДО обрезки. `crop_pt` — сколько
        отрезать с каждой стороны уже повёрнутой страницы, в пойнтах, в порядке
        (слева, снизу, справа, сверху) — так это принимает PDFium.

        `grayscale=True` — растеризация сразу в оттенках серого. Это не только
        экономия памяти: предпросмотр при этом показывает документ ровно таким,
        каким он выйдет из чёрно-белого аппарата, вместе с тем, во что
        превратятся цветные заливки и светлый текст.
        """
        scale = dpi / PT_PER_INCH
        with self._lock:
            page = self._page(number)
            bitmap = page.render(
                scale=scale,
                rotation=rotation % 360,
                crop=crop_pt,
                grayscale=grayscale,
                optimize_mode="print",
                fill_color=(255, 255, 255, 255),
                draw_annots=True,
            )
            image = bitmap.to_pil()
            bitmap.close()
        # to_pil отдаёт RGBA/BGRA; на белой подложке альфа уже не нужна, а вниз
        # по конвейеру (GDI, PNG) удобнее иметь предсказуемые 3 канала.
        return image.convert("RGB")
