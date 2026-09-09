"""
Упаковка растра для принтера.

Написано после того, как задание уходило в аппарат, спулер его принимал и
закрывал, принтер просыпался — и выдавал чистый лист. Формат DIB, который
безупречно работает на экране, драйверы печати принимают не всякий.
"""

from PIL import Image

from pbreader.output.dib import pack


def image(width, height, colour=(10, 20, 30)):
    return Image.new("RGB", (width, height), colour)


class TestRowOrder:
    def test_rows_go_bottom_up(self):
        """Отрицательная высота в заголовке (строки сверху вниз) — законный
        способ, и на экране он работает везде. Драйверы принтеров принимают
        его далеко не все: вызов отвечает успехом, а бумага выходит чистой.
        """
        picture = Image.new("RGB", (4, 2), (255, 255, 255))
        picture.putpixel((0, 0), (0, 0, 0))  # чёрная точка в ЛЕВОМ ВЕРХНЕМ углу
        dib = pack(picture)

        last_row = dib.bits[dib.stride:]
        assert last_row[:3] == b"\x00\x00\x00", "верхняя строка должна оказаться последней"
        assert dib.height > 0, "высота положительная — это и означает «снизу вверх»"

    def test_colours_are_stored_as_bgr(self):
        dib = pack(image(4, 1, (10, 20, 30)))
        assert dib.bits[:3] == bytes((30, 20, 10))


class TestAlignment:
    def test_row_length_is_always_a_multiple_of_four(self):
        """Требование самого формата: невыровненная строка — это мусор на
        бумаге либо пустой лист."""
        for width in range(1, 17):
            dib = pack(image(width, 3))
            assert dib.stride % 4 == 0, f"ширина {width} даёт строку {dib.stride} байт"

    def test_padding_widens_the_header_not_the_picture(self):
        """Дополняем ширину, а не каждую строку по отдельности. Лишние столбцы
        в исходный прямоугольник при выводе не попадают."""
        dib = pack(image(5, 2))
        assert dib.width == 8
        assert dib.source_width == 5

    def test_aligned_width_is_left_alone(self):
        dib = pack(image(8, 2))
        assert dib.width == dib.source_width == 8

    def test_size_matches_the_declared_geometry(self):
        dib = pack(image(7, 5))
        assert len(dib.bits) == dib.stride * dib.height


class TestInput:
    def test_grayscale_is_converted(self):
        dib = pack(Image.new("L", (4, 2), 128))
        assert len(dib.bits) == 4 * 3 * 2

    def test_transparency_is_flattened(self):
        dib = pack(Image.new("RGBA", (4, 2), (10, 20, 30, 255)))
        assert dib.bits[:3] == bytes((30, 20, 10))

    def test_single_pixel_still_aligns(self):
        dib = pack(image(1, 1))
        assert dib.stride == 12 and dib.width == 4
