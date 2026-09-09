import io

import pytest
from PIL import Image, ImageChops

from pbreader.document import PdfDocument
from pbreader.job import ColorMode, Duplex, Orientation, PrintJob, ScaleMode
from pbreader.paper import A4
from pbreader.preview import describe_job, render_sheet_side
from pbreader.sheets import plan_sheets
from pbreader.units import mm_to_pt


def sheets_for(document, job):
    return plan_sheets(job.resolve_pages(document.page_count), job.duplex, job.copies, job.collate)


def render(document, job, device, number=1, side="front", width=400):
    return render_sheet_side(document, job, device, sheets_for(document, job)[number - 1], side, width)


class TestSheetImage:
    def test_preview_is_the_shape_of_the_sheet_not_the_document(self, make_pdf, device):
        """Предпросмотр показывает ЛИСТ. Альбомная страница на книжном листе —
        это книжная картинка с полями, а не альбомная картинка документа."""
        path = make_pdf([(A4.size.height, A4.size.width, 0)])
        job = PrintJob(orientation=Orientation.PORTRAIT)
        with PdfDocument(path) as document:
            preview = render(document, job, device)
        image = Image.open(io.BytesIO(preview.png))
        assert image.height > image.width
        assert image.height / image.width == pytest.approx(297 / 210, rel=0.02)

    def test_landscape_job_gives_a_landscape_sheet(self, a4_portrait_pdf, device):
        job = PrintJob(orientation=Orientation.LANDSCAPE)
        with PdfDocument(a4_portrait_pdf) as document:
            preview = render(document, job, device)
        assert preview.width > preview.height

    def test_blank_back_of_the_last_duplex_sheet_renders_empty(self, make_pdf, device):
        path = make_pdf([(A4.size.width, A4.size.height, 0)] * 3)
        job = PrintJob(duplex=Duplex.LONG_EDGE)
        with PdfDocument(path) as document:
            preview = render(document, job, device, number=2, side="back")
        assert preview.page is None
        image = Image.open(io.BytesIO(preview.png)).convert("L")
        # Чистая сторона: только рамка листа и пунктир полей, содержимого нет.
        assert image.crop((40, 40, image.width - 40, image.height - 40)).getextrema()[0] > 200


class TestColour:
    def test_monochrome_preview_is_actually_monochrome(self, make_pdf, device):
        """Человек, выбравший ч/б, должен увидеть ч/б — включая то, во что
        превратится цветная заливка. Ни Foxit, ни SumatraPDF этого не показывают."""
        path = make_pdf([(A4.size.width, A4.size.height, 0)])
        with PdfDocument(path) as document:
            mono = render(document, PrintJob(color=ColorMode.MONOCHROME), device)
            colour = render(document, PrintJob(color=ColorMode.COLOR), device)

        mono_image = Image.open(io.BytesIO(mono.png)).convert("RGB")
        colour_image = Image.open(io.BytesIO(colour.png)).convert("RGB")
        assert _has_colour(colour_image)
        assert not _has_colour(mono_image, ignore_border=True)


def _has_colour(image: Image.Image, ignore_border: bool = False) -> bool:
    """Есть ли в картинке цветные пиксели (кроме служебной рамки предпросмотра)."""
    region = image.crop((30, 30, image.width - 30, image.height - 30)) if ignore_border else image
    red, green, blue = region.split()
    spread = max(
        ImageChops.difference(red, green).getextrema()[1],
        ImageChops.difference(green, blue).getextrema()[1],
    )
    return spread > 12


class TestClipWarning:
    def test_one_to_one_a4_does_not_cry_wolf(self, a4_portrait_pdf, device):
        """A4 один к одному геометрически всегда выходит на непечатаемые поля,
        но изображения там нет. Предупреждать об этом каждый раз — значит
        приучить человека не читать предупреждения вовсе."""
        with PdfDocument(a4_portrait_pdf) as document:
            preview = render(document, PrintJob(scale=ScaleMode.ACTUAL), device)
        assert preview.is_clipped
        assert not preview.ink_clipped
        assert not any("не напечатается" in w for w in preview.warnings)

    def test_real_loss_is_reported(self, make_pdf, device):
        """А вот когда за поля уходит содержимое — об этом надо сказать прямо."""
        big = A4.size.width * 1.6
        path = make_pdf([(big, big, 0)])
        with PdfDocument(path) as document:
            preview = render(document, PrintJob(scale=ScaleMode.ACTUAL), device)
        assert preview.ink_clipped
        assert any("не напечатается" in w for w in preview.warnings)

    def test_content_that_falls_off_the_sheet_entirely_is_still_reported(self, make_pdf, device):
        """Самый тяжёлый случай обрезки: содержимое уходит за край бумаги целиком.

        На собранный лист оно тогда вообще не попадает, и по листу потерю не
        увидеть — поэтому проверять надо растр страницы, а не лист.
        """
        path = make_pdf([(A4.size.width * 1.5, A4.size.height * 1.5, 0)])
        with PdfDocument(path) as document:
            preview = render(document, PrintJob(scale=ScaleMode.ACTUAL), device)
        assert preview.ink_clipped

    @pytest.mark.parametrize("width", [120, 200, 400, 900, 1600])
    def test_the_verdict_does_not_depend_on_the_preview_size(self, a4_portrait_pdf, device, width):
        """Один и тот же документ не может «терять текст» в миниатюре и не
        терять в полном виде.

        При уменьшении картинки сглаживание размазывает содержимое на пиксель
        за границу области печати, и проверка по одному тёмному пикселю
        объявляла потерю там, где её нет: в ленте листов все миниатюры
        краснели, хотя терять было нечего.
        """
        with PdfDocument(a4_portrait_pdf) as document:
            preview = render(document, PrintJob(scale=ScaleMode.ACTUAL), device, width=width)
        assert preview.ink_clipped is False

    @pytest.mark.parametrize("width", [120, 400, 1600])
    def test_real_loss_is_seen_at_every_size_too(self, make_pdf, device, width):
        big = A4.size.width * 1.25
        path = make_pdf([(big, A4.size.height * 1.25, 0)])
        with PdfDocument(path) as document:
            preview = render(document, PrintJob(scale=ScaleMode.ACTUAL), device, width=width)
        assert preview.ink_clipped is True

    def test_estimated_margins_are_disclosed(self, a4_portrait_pdf, device):
        with PdfDocument(a4_portrait_pdf) as document:
            preview = render(document, PrintJob(), device)
        assert any("приблизительно" in w for w in preview.warnings)


class TestDescribeJob:
    def test_counts_sheets_not_pages(self, make_pdf, device):
        """Человек у аппарата считает листы: он их заберёт и за них заплатит."""
        path = make_pdf([(A4.size.width, A4.size.height, 0)] * 5)
        job = PrintJob(duplex=Duplex.LONG_EDGE, copies=2)
        with PdfDocument(path) as document:
            description = describe_job(document, job, device)
        assert description["document"]["page_count"] == 5
        assert description["sheet_count"] == 6  # по 3 листа на копию, оборот последнего пуст

    def test_reports_the_scale_that_will_be_applied(self, make_pdf, device):
        path = make_pdf([(mm_to_pt(148), mm_to_pt(210), 0)])
        with PdfDocument(path) as document:
            description = describe_job(document, PrintJob(), device)
        assert description["scale"]["mode"] == "fit"
        assert description["scale"]["percent"] > 100

    def test_reports_margins_and_whether_they_were_measured(self, a4_portrait_pdf, device):
        with PdfDocument(a4_portrait_pdf) as document:
            description = describe_job(document, PrintJob(), device)
        assert description["paper"]["margins_mm"]["left"] == pytest.approx(4.2, abs=0.1)
        assert description["paper"]["margins_measured"] is False
