"""
Заказы: раскладка, цена и границы вокруг денег.

Тут проверяется то, что при ошибке стоит денег или доверия: цена, пришедшая от
клиента, чистый оборот листа, чужой заказ, правка после подтверждения и смена
тарифа под уже принятым заказом.
"""

import pytest
from fastapi.testclient import TestClient

from printhub import db, jobs
from printhub.api.app import create_app
from printhub.config import Settings
from printhub.models import OrderState

from helpers import login, upload


@pytest.fixture
def settings(tmp_path):
    return Settings(
        env="dev",
        bot_token="123456:ТЕСТОВЫЙ-ТОКЕН",
        secret_key="ключ-для-теста",
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        max_upload_bytes=8 * 1024 * 1024,
        dev_login=False,
        admin_ids=frozenset({9000}),
    )


@pytest.fixture
def client(settings):
    db._Session = None
    jobs.forget_cached_coverage()
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def printer(client):
    """Точка печати заводится админом — как и в жизни."""
    admin = login(client, 9000, "Админ")
    response = client.post(
        "/api/admin/printers",
        headers=admin,
        json={"id": "point-1", "title": "Точка 1", "color_supported": True,
              "duplex_supported": True, "paper": "A4"},
    )
    assert response.status_code == 200, response.text
    client.put(
        "/api/admin/tariffs/default",
        headers=admin,
        json={"price_mono": 30, "price_color": 150, "heavy_ink_from": 0.3,
              "heavy_extra_mono": 30, "heavy_extra_color": 150, "min_order": 0},
    )
    return "point-1"


def order_for(client, headers, path, printer_id, **options):
    file_id = upload(client, headers, path).json()["id"]
    response = client.post(
        "/api/orders",
        headers=headers,
        json={"file_id": file_id, "printer_id": printer_id, "options": options},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestPlan:
    def test_a_three_page_document_costs_three_sides(self, client, printer, make_pdf):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(3), printer)
        assert order["plan"]["sheet_count"] == 3
        assert order["plan"]["printed_sides"] == 3
        assert order["amount"] == 90

    def test_duplex_halves_the_sheets_but_not_the_price(self, client, printer, make_pdf):
        """Бумаги уходит вдвое меньше, тонера — столько же."""
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(4), printer, duplex="long-edge")
        assert order["plan"]["sheet_count"] == 2
        assert order["amount"] == 120

    def test_a_blank_back_is_not_charged(self, client, printer, make_pdf):
        """Нечётная страница в дуплексе оставляет чистый оборот.

        Он не печатается — и в счёт попасть не должен: три страницы дуплексом
        это два листа, но по-прежнему три запечатанные стороны.
        """
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(3), printer, duplex="long-edge")
        assert order["plan"]["sheet_count"] == 2
        assert order["plan"]["printed_sides"] == 3
        assert order["amount"] == 90

    def test_copies_multiply_everything(self, client, printer, make_pdf):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(2), printer, copies=3)
        assert order["plan"]["sheet_count"] == 6
        assert order["amount"] == 180

    def test_a_page_range_is_honoured(self, client, printer, make_pdf):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(10), printer, pages="2-4")
        assert order["plan"]["printed_sides"] == 3
        assert order["amount"] == 90

    def test_colour_costs_the_colour_rate(self, client, printer, make_pdf):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(2), printer, color="color")
        assert order["amount"] == 300

    def test_the_margins_are_marked_as_not_measured(self, client, printer, make_pdf):
        """Настоящие поля знает драйвер, а принтера пока нет.

        Показать поля как измеренные значило бы соврать: интерфейс обязан это
        отметить, а когда агент пришлёт свою геометрию — флаг станет true сам.
        """
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(1), printer)
        assert order["plan"]["paper"]["margins_measured"] is False


class TestPriceIsServerSide:
    def test_the_client_cannot_name_its_own_price(self, client, printer, make_pdf):
        """Веб-апп — код на чужом телефоне. «Итого 1 ₸» подделывается за минуту."""
        headers = login(client, 42)
        file_id = upload(client, headers, make_pdf(4)).json()["id"]
        response = client.post(
            "/api/orders",
            headers=headers,
            json={
                "file_id": file_id,
                "printer_id": printer,
                "options": {"amount": 1, "price": 1, "currency": "USD"},
            },
        )
        assert response.status_code == 200
        assert response.json()["amount"] == 120
        assert response.json()["currency"] == "KZT"

    def test_patching_options_recalculates_the_price(self, client, printer, make_pdf):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(2), printer)
        assert order["amount"] == 60
        updated = client.patch(
            f"/api/orders/{order['id']}", headers=headers, json={"options": {"copies": 4}}
        ).json()
        assert updated["amount"] == 240

    def test_the_breakdown_matches_the_total(self, client, printer, make_pdf):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(3), printer, color="color")
        assert sum(line["amount"] for line in order["price"]["lines"]) == order["amount"]


class TestConfirmed:
    def test_a_confirmed_order_waits_for_payment(self, client, printer, make_pdf):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(1), printer)
        confirmed = client.post(f"/api/orders/{order['id']}/confirm", headers=headers).json()
        assert confirmed["state"] == OrderState.AWAITING_PAYMENT
        assert confirmed["editable"] is False

    def test_a_confirmed_order_cannot_be_edited(self, client, printer, make_pdf):
        """Иначе цену можно было бы уронить после того, как её показали."""
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(4), printer)
        client.post(f"/api/orders/{order['id']}/confirm", headers=headers)
        response = client.patch(
            f"/api/orders/{order['id']}", headers=headers, json={"options": {"copies": 1}}
        )
        assert response.status_code == 409

    def test_a_new_tariff_does_not_change_a_confirmed_order(self, client, printer, make_pdf):
        """Админ меняет цены когда угодно. Принятый заказ обязан стоить столько,
        сколько человеку показали."""
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(2), printer)
        confirmed = client.post(f"/api/orders/{order['id']}/confirm", headers=headers).json()
        assert confirmed["amount"] == 60

        client.put(
            "/api/admin/tariffs/default",
            headers=login(client, 9000, "Админ"),
            json={"price_mono": 500, "price_color": 900, "heavy_ink_from": 0.3},
        )
        again = client.get(f"/api/orders/{order['id']}", headers=headers).json()
        assert again["amount"] == 60

    def test_a_tariff_change_does_apply_to_a_draft(self, client, printer, make_pdf):
        """А вот черновик считается по сегодняшнему прайсу — до подтверждения."""
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(2), printer)
        client.put(
            "/api/admin/tariffs/default",
            headers=login(client, 9000, "Админ"),
            json={"price_mono": 50, "price_color": 150, "heavy_ink_from": 0.3},
        )
        confirmed = client.post(f"/api/orders/{order['id']}/confirm", headers=headers).json()
        assert confirmed["amount"] == 100


class TestOtherPeoplesOrders:
    @pytest.fixture
    def foreign(self, client, printer, make_pdf):
        return order_for(client, login(client, 100, "Первый"), make_pdf(1), printer)["id"]

    def test_a_stranger_does_not_see_it(self, client, foreign):
        assert client.get("/api/orders", headers=login(client, 200)).json()["orders"] == []

    def test_a_stranger_cannot_open_it(self, client, foreign):
        assert client.get(f"/api/orders/{foreign}", headers=login(client, 200)).status_code == 404

    def test_a_stranger_cannot_edit_it(self, client, foreign):
        response = client.patch(
            f"/api/orders/{foreign}", headers=login(client, 200), json={"options": {"copies": 9}}
        )
        assert response.status_code == 404

    def test_a_stranger_cannot_see_its_preview(self, client, foreign):
        """Предпросмотр — это содержимое документа. Утечка здесь равна утечке файла."""
        response = client.get(f"/api/orders/{foreign}/preview/1", headers=login(client, 200))
        assert response.status_code == 404

    def test_an_order_cannot_be_made_from_someone_elses_file(self, client, printer, make_pdf):
        stranger_file = upload(client, login(client, 100, "Первый"), make_pdf(1)).json()["id"]
        response = client.post(
            "/api/orders",
            headers=login(client, 200),
            json={"file_id": stranger_file, "printer_id": printer},
        )
        assert response.status_code == 404


class TestPreview:
    def test_a_sheet_comes_back_as_a_png(self, client, printer, make_pdf):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(2), printer)
        response = client.get(f"/api/orders/{order['id']}/preview/1", headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_asking_for_a_sheet_that_does_not_exist_says_so(self, client, printer, make_pdf):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(1), printer)
        assert client.get(f"/api/orders/{order['id']}/preview/9", headers=headers).status_code == 404

    def test_the_requested_width_is_capped(self, client, printer, make_pdf):
        """Ширина приходит из адреса: без предела один запрос съел бы сервер."""
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(1), printer)
        response = client.get(
            f"/api/orders/{order['id']}/preview/1?width=50000", headers=headers
        )
        assert response.status_code == 200
        assert len(response.content) < 4 * 1024 * 1024


class TestUnsupported:
    def test_a_word_file_says_what_is_missing_instead_of_failing(self, client, printer, tmp_path):
        """Конвертация живёт на машине у принтера — её пока нет.

        Показать пустой лист было бы хуже отказа: человек решил бы, что сломан
        его файл.
        """
        headers = login(client, 42)
        import zipfile

        docx = tmp_path / "резюме.docx"
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml", "<w:document/>")
            archive.writestr("[Content_Types].xml", "<Types/>")
        file_id = upload(client, headers, docx).json()["id"]
        response = client.post(
            "/api/orders", headers=headers, json={"file_id": file_id, "printer_id": printer}
        )
        assert response.status_code == 409
        assert "PDF" in response.json()["error"]


class TestDuplexDiscount:
    """Скидка за двустороннюю печать: бумаги уходит вдвое меньше."""

    @pytest.fixture
    def with_discount(self, client):
        admin = login(client, 9000, "Админ")
        client.put("/api/admin/tariffs/default", headers=admin,
                   json={"price_mono": 60, "price_color": 200, "duplex_discount": 5,
                         "heavy_ink_from": 0.3, "heavy_extra_mono": 0, "heavy_extra_color": 0})
        return "point-1"

    def test_one_sided_printing_gets_no_discount(self, client, printer, with_discount, make_pdf):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(4), printer)
        assert order["amount"] == 240

    def test_duplex_takes_the_discount_off_every_paired_side(
        self, client, printer, with_discount, make_pdf
    ):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(4), printer, duplex="long-edge")
        assert order["amount"] == 228          # 240 − 5 %
        assert any("двусторон" in line["title"] for line in order["price"]["lines"])

    def test_the_odd_last_side_is_not_discounted(self, client, printer, with_discount, make_pdf):
        """Последняя нечётная страница едет на отдельном листе и бумаги
        не экономит — скидка ей не положена."""
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(3), printer, duplex="long-edge")
        # Две стороны со скидкой, одна без: 180 − 5 % от 120.
        assert order["amount"] == 174

    def test_the_discount_line_keeps_the_receipt_honest(
        self, client, printer, with_discount, make_pdf
    ):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(4), printer, duplex="long-edge")
        assert sum(line["amount"] for line in order["price"]["lines"]) == order["amount"]

    def test_a_discount_of_a_hundred_percent_is_refused(self, client):
        """Сто процентов — бесплатная печать, больше — доплата клиенту."""
        response = client.put("/api/admin/tariffs/default", headers=login(client, 9000, "Админ"),
                              json={"price_mono": 60, "price_color": 200, "duplex_discount": 100})
        assert response.status_code == 400


class TestPageThumbnails:
    def test_a_page_comes_back_as_a_picture(self, client, printer, make_pdf):
        """Страницы выбираются галочками по картинкам, а не строкой «1-5»."""
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(3), printer)
        response = client.get(f"/api/orders/{order['id']}/pages/2", headers=headers)
        assert response.status_code == 200
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_the_thumbnail_shows_that_page_whatever_the_layout(
        self, client, printer, make_pdf
    ):
        """Дуплекс и копии меняют раскладку листов, но третья страница
        остаётся третьей страницей."""
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(4), printer,
                          duplex="long-edge", copies=3)
        assert client.get(f"/api/orders/{order['id']}/pages/4",
                          headers=headers).status_code == 200

    def test_a_stranger_gets_nothing(self, client, printer, make_pdf):
        order = order_for(client, login(client, 100, "Первый"), make_pdf(2), printer)
        response = client.get(f"/api/orders/{order['id']}/pages/1", headers=login(client, 200))
        assert response.status_code == 404


class TestAbandonedDrafts:
    """Черновик — это открытый экран настроек, а не заказ."""

    def test_an_abandoned_draft_leaves_no_trace(self, client, printer, make_pdf):
        """Человек открыл файл, передумал и закрыл. Записи о несостоявшемся
        заказе в истории быть не должно."""
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(1), printer)
        assert client.delete(f"/api/orders/{order['id']}", headers=headers).status_code == 200
        assert client.get("/api/orders", headers=headers).json()["orders"] == []

    def test_a_confirmed_order_is_cancelled_but_kept(self, client, printer, make_pdf):
        """А вот подтверждённый заказ — уже событие: его отменяют, а не стирают."""
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(1), printer)
        client.post(f"/api/orders/{order['id']}/confirm", headers=headers)
        client.delete(f"/api/orders/{order['id']}", headers=headers)
        listed = client.get("/api/orders", headers=headers).json()["orders"]
        assert [row["state"] for row in listed] == [OrderState.CANCELLED]

    def test_a_stranger_cannot_delete_a_draft(self, client, printer, make_pdf):
        order = order_for(client, login(client, 100, "Первый"), make_pdf(1), printer)
        assert client.delete(f"/api/orders/{order['id']}",
                             headers=login(client, 200)).status_code == 404


class TestRotation:
    def test_turning_the_sheet_turns_the_content_with_it(self, client, printer, make_pdf):
        """Иначе книжная страница на альбомном листе просто съёжилась бы —
        и это выглядит как поломка, а не как поворот."""
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(1), printer, orientation="landscape")
        # Масштаб остаётся близким к единице: содержимое повернулось вместе
        # с листом, а не вписалось в него боком.
        assert order["plan"]["scale"]["percent"] > 90

    def test_without_rotation_the_page_would_shrink(self, client, printer, make_pdf):
        headers = login(client, 42)
        order = order_for(client, headers, make_pdf(1), printer,
                          orientation="landscape", auto_rotate=False)
        assert order["plan"]["scale"]["percent"] < 80
