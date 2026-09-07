"""
Рисует значок программы (.ico) — без графических редакторов и внешних файлов.

Значок собирается кодом, чтобы сборка не зависела от бинарника в репозитории и
чтобы его можно было поправить, не открывая ничего, кроме этого файла.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

#: Windows берёт из .ico подходящий размер сам: 16 — для списка задач, 256 — для
#: крупных значков в проводнике.
SIZES = (16, 24, 32, 48, 64, 128, 256)

BLUE = (47, 111, 208, 255)
PAPER = (255, 255, 255, 255)
SHEET = (207, 224, 247, 255)


def draw(size: int) -> Image.Image:
    """Лист бумаги, выходящий из аппарата, — на скруглённом синем поле."""
    scale = 8  # рисуем крупнее и уменьшаем: так края получаются гладкими
    canvas = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    unit = size * scale / 32

    draw.rounded_rectangle([0, 0, size * scale - 1, size * scale - 1], radius=7 * unit, fill=BLUE)
    draw.rounded_rectangle([9 * unit, 5 * unit, 23 * unit, 15 * unit], radius=1.5 * unit, fill=PAPER)
    draw.rounded_rectangle([6.5 * unit, 12 * unit, 25.5 * unit, 22 * unit], radius=2 * unit, fill=SHEET)
    draw.rounded_rectangle([9 * unit, 18 * unit, 23 * unit, 27 * unit], radius=1.5 * unit, fill=PAPER)
    return canvas.resize((size, size), Image.LANCZOS)


def build(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    images = [draw(size) for size in SIZES]
    images[-1].save(destination, format="ICO", sizes=[(s, s) for s in SIZES])
    return destination


if __name__ == "__main__":
    path = build(Path(__file__).resolve().parent / "pbreader.ico")
    print(f"Значок собран: {path}")
