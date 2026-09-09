"""
Упаковка растра в DIB — в том виде, который принимают драйверы принтеров.

Вынесено из gdi.py отдельно по двум причинам: здесь нет ни одного вызова Win32,
поэтому упаковку можно проверять тестами где угодно, — а именно она и оказалась
местом, где задание уходило в аппарат и не печаталось.

Два решения, оба по итогам той печати:

* СНИЗУ ВВЕРХ, а не сверху вниз. Отрицательная высота в заголовке DIB — законный
  способ задать порядок строк сверху вниз, и на экране он работает везде. Но
  драйверы принтеров поддерживают его далеко не все: вызов при этом отвечает
  успехом, а на бумаге пусто. Обычный порядок строк понимают все.
* 24 бита, а не 32. То же самое: 32-битный BI_RGB многие драйверы печати не
  разбирают, хотя для экрана он совершенно обычен.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class Dib:
    """Готовые к передаче в GDI биты и их размеры."""

    bits: bytes
    #: Ширина В ЗАГОЛОВКЕ — может быть больше настоящей из-за выравнивания.
    width: int
    height: int
    #: Настоящий размер картинки: столько и надо брать в исходном прямоугольнике.
    source_width: int
    source_height: int

    @property
    def stride(self) -> int:
        return self.width * 3


def pack(image: Image.Image) -> Dib:
    """Готовит картинку к выводу на принтер.

    Строка DIB обязана быть кратна четырём байтам. Вместо дополнения каждой
    строки по отдельности дополняем ШИРИНУ до кратной четырём пикселям: при
    24 битах строка тогда выравнивается сама (4 px x 3 = 12 байт), а лишние
    столбцы просто не попадают в исходный прямоугольник при выводе.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")

    source_width, source_height = image.width, image.height
    padded_width = (source_width + 3) // 4 * 4
    if padded_width != source_width:
        padded = Image.new("RGB", (padded_width, source_height), (255, 255, 255))
        padded.paste(image, (0, 0))
        image = padded

    flipped = image.transpose(Image.FLIP_TOP_BOTTOM)
    return Dib(
        bits=flipped.tobytes("raw", "BGR"),
        width=padded_width,
        height=source_height,
        source_width=source_width,
        source_height=source_height,
    )
