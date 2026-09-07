"""Общие приспособления для тестов: генератор PDF без внешних зависимостей."""

from __future__ import annotations

from pathlib import Path

import pytest

from pbreader.geometry import DeviceGeometry
from pbreader.paper import A4
from pbreader.units import mm_to_pt


def _build_pdf(pages: list[tuple[float, float, int]]) -> bytes:
    """Минимальный валидный PDF: рамка и заливка на каждой странице.

    Пишется руками, чтобы тесты не зависели от библиотеки генерации PDF, а
    /Rotate можно было задать точно таким, каким его ставят сканеры.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    content_ids = []
    for width, height, _ in pages:
        stream = (
            f"1 0 0 RG 4 w 20 20 {width - 40:.2f} {height - 40:.2f} re S "
            f"0 0 1 rg 60 60 120 60 re f"
        ).encode()
        content_ids.append(add(b"<< /Length %d >>stream\n%s\nendstream" % (len(stream), stream)))

    page_ids = []
    for index, (width, height, rotate) in enumerate(pages):
        page_ids.append(
            add(
                b"<< /Type /Page /Parent @PAGES@ /MediaBox [0 0 %.2f %.2f] /Rotate %d "
                b"/Contents %d 0 R /Resources << >> >>" % (width, height, rotate, content_ids[index])
            )
        )
    pages_id = add(
        b"<< /Type /Pages /Count %d /Kids [%s] >>"
        % (len(page_ids), b" ".join(b"%d 0 R" % pid for pid in page_ids))
    )
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body.replace(b"@PAGES@", b"%d 0 R" % pages_id) + b"\nendobj\n"

    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, catalog_id, xref_at
    )
    return bytes(out)


@pytest.fixture
def make_pdf(tmp_path: Path):
    counter = {"n": 0}

    def factory(pages: list[tuple[float, float, int]], name: str | None = None) -> Path:
        counter["n"] += 1
        path = tmp_path / (name or f"doc{counter['n']}.pdf")
        path.write_bytes(_build_pdf(pages))
        return path

    return factory


@pytest.fixture
def a4_portrait_pdf(make_pdf):
    return make_pdf([(A4.size.width, A4.size.height, 0)] * 3)


@pytest.fixture
def device() -> DeviceGeometry:
    """Типовая геометрия A4 с симметричными полями 4.2 мм."""
    return DeviceGeometry.nominal(A4)


@pytest.fixture
def asymmetric_device() -> DeviceGeometry:
    """A4 с несимметричными полями — как у реального лазерника: снизу шире.

    Именно на таком листе видно, правильно ли раскладка переносит область
    печати между книжной и альбомной ориентацией.
    """
    from pbreader.units import Rect

    paper = A4.size
    return DeviceGeometry(
        paper_size=paper,
        printable=Rect(
            mm_to_pt(4.0),
            mm_to_pt(3.0),
            paper.width - mm_to_pt(4.0 + 5.0),
            paper.height - mm_to_pt(3.0 + 10.0),
        ),
        dpi_x=600,
        dpi_y=600,
    )
