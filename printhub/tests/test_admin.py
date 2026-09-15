"""
Админка: цены задаём мы, и только мы.

Тариф — это прямой доступ к выручке: кто его правит, тот назначает, сколько
стоит печать. Поэтому проверяется не только «админ может», но и каждая попытка
обойтись без прав.
"""

import pytest
from fastapi.testclient import TestClient
from helpers import login, upload

from printhub import db, jobs
from printhub.api.app import create_app
from printhub.config import Settings

ADMIN = 9000
STRANGER = 42


@pytest.fixture
def settings(tmp_path):
    return Settings(
        env="dev",
        bot_token="123456:ТЕСТОВЫЙ-ТОКЕН",
        secret_key="ключ-для-теста",
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        dev_login=False,
        admin_ids=frozenset({ADMIN}),
    )


@pytest.fixture
def client(settings):
    db._Session = None
    jobs.forget_cached_coverage()
    with TestClient(create_app(settings)) as test_client:
        yield test_client


class TestWhoGetsIn:
    @pytest.mark.parametrize("path", [
        "/api/admin/printers",
        "/api/admin/tariffs",
        "/api/admin/orders",
    ])
    def test_an_ordinary_user_does_not_find_the_panel(self, client, path):
        """404, а не 403: подтверждать существование панели незачем."""
        assert client.get(path, headers=login(client, STRANGER)).status_code == 404

    def test_an_ordinary_user_cannot_set_prices(self, client):
        response = client.put(
            "/api/admin/tariffs/default",
            headers=login(client, STRANGER),
            json={"price_mono": 1, "price_color": 1},
        )
        assert response.status_code == 404

    def test_without_a_session_there_is_nothing_at_all(self, client):
        assert client.get("/api/admin/tariffs").status_code == 401

    def test_the_admin_gets_in(self, client):
        assert client.get("/api/admin/tariffs", headers=login(client, ADMIN)).status_code == 200

    def test_the_profile_says_who_is_an_admin(self, client):
        """По этому признаку интерфейс решает, показывать ли вкладку."""
        assert client.get("/api/me", headers=login(client, ADMIN)).json()["is_admin"] is True
        assert client.get("/api/me", headers=login(client, STRANGER)).json()["is_admin"] is False


class TestTariff:
    def test_a_default_tariff_exists_from_the_first_start(self, client):
        """Без него первый заказ считался бы по ценам, зашитым в код."""
        tariffs = client.get("/api/admin/tariffs", headers=login(client, ADMIN)).json()["tariffs"]
        assert any(row["printer_id"] is None for row in tariffs)

    def test_the_admin_changes_the_price_and_it_applies(self, client, make_pdf):
        admin = login(client, ADMIN)
        client.put("/api/admin/tariffs/default", headers=admin,
                   json={"price_mono": 45, "price_color": 200, "heavy_ink_from": 0.3})
        client.post("/api/admin/printers", headers=admin, json={"id": "p1", "title": "П1"})

        headers = login(client, STRANGER)
        file_id = upload(client, headers, make_pdf(2)).json()["id"]
        order = client.post("/api/orders", headers=headers,
                            json={"file_id": file_id, "printer_id": "p1"}).json()
        assert order["amount"] == 90

    def test_a_printer_can_have_its_own_prices(self, client, make_pdf):
        """Точка в центре и точка в общежитии не обязаны стоить одинаково."""
        admin = login(client, ADMIN)
        client.post("/api/admin/printers", headers=admin, json={"id": "cheap", "title": "Дешёвая"})
        client.post("/api/admin/printers", headers=admin, json={"id": "dear", "title": "Дорогая"})
        client.put("/api/admin/tariffs/default", headers=admin,
                   json={"price_mono": 30, "price_color": 150, "heavy_ink_from": 0.3})
        client.put("/api/admin/tariffs/dear", headers=admin,
                   json={"price_mono": 100, "price_color": 400, "heavy_ink_from": 0.3})

        headers = login(client, STRANGER)
        file_id = upload(client, headers, make_pdf(1)).json()["id"]
        cheap = client.post("/api/orders", headers=headers,
                            json={"file_id": file_id, "printer_id": "cheap"}).json()
        dear = client.post("/api/orders", headers=headers,
                           json={"file_id": file_id, "printer_id": "dear"}).json()
        assert cheap["amount"] == 30
        assert dear["amount"] == 100

    @pytest.mark.parametrize("payload", [
        {"price_mono": -1, "price_color": 10},
        {"price_mono": 10, "price_color": -5},
        {"price_mono": 10, "price_color": 10, "min_order": -100},
        {"price_mono": 10, "price_color": 10, "heavy_ink_from": 0},
        {"price_mono": 10, "price_color": 10, "heavy_ink_from": 1.5},
    ])
    def test_nonsense_prices_are_refused(self, client, payload):
        """Отрицательная цена — это доплата клиенту за печать."""
        response = client.put("/api/admin/tariffs/default", headers=login(client, ADMIN),
                              json=payload)
        assert response.status_code == 400

    def test_a_tariff_for_a_printer_that_does_not_exist_is_refused(self, client):
        response = client.put("/api/admin/tariffs/выдуманный", headers=login(client, ADMIN),
                              json={"price_mono": 10, "price_color": 10})
        assert response.status_code == 404


class TestPrinters:
    def test_a_new_printer_appears_for_everyone(self, client):
        admin = login(client, ADMIN)
        client.post("/api/admin/printers", headers=admin,
                    json={"id": "hall", "title": "В холле", "location": "1 этаж"})
        listed = client.get("/api/printers", headers=login(client, STRANGER)).json()["printers"]
        assert [row["id"] for row in listed] == ["hall"]
        assert listed[0]["margins_measured"] is False

    def test_a_printer_needs_a_code(self, client):
        """Код попадёт в ссылку с QR на корпусе — без него точку не выбрать."""
        response = client.post("/api/admin/printers", headers=login(client, ADMIN),
                               json={"title": "Безымянная"})
        assert response.status_code == 400

    def test_two_printers_cannot_share_a_code(self, client):
        admin = login(client, ADMIN)
        client.post("/api/admin/printers", headers=admin, json={"id": "one", "title": "Первая"})
        response = client.post("/api/admin/printers", headers=admin,
                               json={"id": "one", "title": "Вторая"})
        assert response.status_code == 409

    def test_a_switched_off_printer_disappears_from_the_list(self, client):
        """Аппарат увезли в ремонт — заказы на него принимать нельзя."""
        admin = login(client, ADMIN)
        client.post("/api/admin/printers", headers=admin, json={"id": "gone", "title": "Убрали"})
        client.patch("/api/admin/printers/gone", headers=admin,
                     json={"title": "Убрали", "is_active": False})
        assert client.get("/api/printers", headers=login(client, STRANGER)).json()["printers"] == []

    def test_a_mono_printer_cannot_be_asked_for_colour(self, client, make_pdf):
        """Иначе человек увидел бы цветной предпросмотр, заплатил по цветному
        тарифу и получил чёрно-белый лист."""
        admin = login(client, ADMIN)
        client.post("/api/admin/printers", headers=admin,
                    json={"id": "mono", "title": "Ч/б", "color_supported": False})
        client.put("/api/admin/tariffs/default", headers=admin,
                   json={"price_mono": 30, "price_color": 150, "heavy_ink_from": 0.3})

        headers = login(client, STRANGER)
        file_id = upload(client, headers, make_pdf(1)).json()["id"]
        order = client.post("/api/orders", headers=headers,
                            json={"file_id": file_id, "printer_id": "mono",
                                  "options": {"color": "color"}}).json()
        assert order["options"]["color"] == "monochrome"
        assert order["amount"] == 30


class TestOversight:
    def test_the_admin_sees_orders_of_all_users(self, client, make_pdf):
        admin = login(client, ADMIN)
        client.post("/api/admin/printers", headers=admin, json={"id": "p", "title": "П"})
        for user_id in (11, 22):
            headers = login(client, user_id, f"Ю{user_id}")
            file_id = upload(client, headers, make_pdf(1)).json()["id"]
            client.post("/api/orders", headers=headers,
                        json={"file_id": file_id, "printer_id": "p"})

        listed = client.get("/api/admin/orders", headers=admin).json()["orders"]
        assert {row["user_id"] for row in listed} == {11, 22}
