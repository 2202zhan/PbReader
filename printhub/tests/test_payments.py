"""
Оплата. Здесь ошибка стоит денег — своих или чужих.

Проверяется то, чем отличается работающий приём платежей от опасного: что
подтверждает оплату, что происходит при повторной доставке, что будет, если
сумма не сошлась, и может ли клиент объявить себя оплатившим.
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from helpers import login, upload

from printhub import db, jobs
from printhub.api.app import create_app
from printhub.config import Settings
from printhub.models import OrderState, PaymentState

SECRET = "общий-секрет-с-сервисом-kaspi"
ADMIN = 9000


@pytest.fixture
def settings(tmp_path):
    return Settings(
        env="dev",
        bot_token="123456:ТЕСТОВЫЙ-ТОКЕН",
        secret_key="ключ-для-теста",
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        dev_login=True,          # платежи понарошку
        admin_ids=frozenset({ADMIN}),
        kaspi_webhook_secret=SECRET,
    )


@pytest.fixture
def client(settings):
    db._Session = None
    jobs.forget_cached_coverage()
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def confirmed(client, make_pdf):
    """Заказ, доведённый до «ждёт оплаты»."""
    admin = login(client, ADMIN, "Админ")
    client.post("/api/admin/printers", headers=admin, json={"id": "p", "title": "Точка"})
    client.put("/api/admin/tariffs/default", headers=admin,
               json={"price_mono": 30, "price_color": 150, "heavy_ink_from": 0.3})

    headers = login(client, 42)
    file_id = upload(client, headers, make_pdf(2)).json()["id"]
    order = client.post("/api/orders", headers=headers,
                        json={"file_id": file_id, "printer_id": "p"}).json()
    client.post(f"/api/orders/{order['id']}/confirm", headers=headers)
    return headers, order["id"], 60


def webhook(client, external_id, event="payment.success", amount=60, secret=SECRET):
    body = json.dumps({
        "event": event,
        "paymentId": external_id,
        "type": "qr",
        "status": "Success",
        "amount": amount,
        "receiptUrl": "https://kaspi.kz/receipt/1",
    }).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/payments/kaspi/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Webhook-Signature": signature},
    )



class TestInvoice:
    def test_a_confirmed_order_can_be_paid(self, client, confirmed):
        headers, order_id, amount = confirmed
        response = client.post(f"/api/orders/{order_id}/pay", headers=headers)
        assert response.status_code == 200
        assert response.json()["amount"] == amount
        assert response.json()["state"] == PaymentState.PENDING
        assert response.json()["pay_url"]

    def test_a_draft_cannot_be_paid(self, client, make_pdf):
        """Пока параметры правятся, сумма ещё может измениться."""
        admin = login(client, ADMIN, "Админ")
        client.post("/api/admin/printers", headers=admin, json={"id": "p", "title": "Точка"})
        headers = login(client, 42)
        file_id = upload(client, headers, make_pdf(1)).json()["id"]
        order = client.post("/api/orders", headers=headers,
                            json={"file_id": file_id, "printer_id": "p"}).json()
        assert client.post(f"/api/orders/{order['id']}/pay", headers=headers).status_code == 409

    def test_asking_twice_returns_the_same_invoice(self, client, confirmed):
        """Иначе на один заказ выпускалось бы несколько QR — и человек,
        вернувшийся к старому экрану, заплатил бы дважды."""
        headers, order_id, _ = confirmed
        first = client.post(f"/api/orders/{order_id}/pay", headers=headers).json()
        second = client.post(f"/api/orders/{order_id}/pay", headers=headers).json()
        assert first["id"] == second["id"]

    def test_a_stranger_cannot_pay_for_someone_elses_order(self, client, confirmed):
        _, order_id, _ = confirmed
        response = client.post(f"/api/orders/{order_id}/pay", headers=login(client, 200))
        assert response.status_code == 404


class TestConfirmation:
    def test_the_webhook_marks_the_order_paid(self, client, confirmed):
        headers, order_id, _ = confirmed
        payment = client.post(f"/api/orders/{order_id}/pay", headers=headers).json()
        external = _external(client, payment["id"])

        assert webhook(client, external).status_code == 200
        state = client.get(f"/api/orders/{order_id}/payment", headers=headers).json()
        assert state["payment"]["state"] == PaymentState.PAID
        assert state["order_state"] == OrderState.PAID
        assert state["payment"]["receipt_url"]

    def test_the_client_cannot_declare_itself_paid(self, client, confirmed):
        """«Я оплатил» с чужого телефона — это просто текст."""
        headers, order_id, _ = confirmed
        client.post(f"/api/orders/{order_id}/pay", headers=headers)
        for attempt in (
            {"state": "paid"},
            {"options": {"state": "paid"}},
        ):
            client.patch(f"/api/orders/{order_id}", headers=headers, json=attempt)
        state = client.get(f"/api/orders/{order_id}/payment", headers=headers).json()
        assert state["order_state"] == OrderState.AWAITING_PAYMENT

    def test_an_unsigned_webhook_is_refused(self, client, confirmed):
        """Адрес вебхука открытый — иначе сервис до него не достучится.
        Всё доверие держится на подписи."""
        headers, order_id, _ = confirmed
        payment = client.post(f"/api/orders/{order_id}/pay", headers=headers).json()
        external = _external(client, payment["id"])

        body = json.dumps({"event": "payment.success", "paymentId": external, "amount": 60})
        response = client.post("/api/payments/kaspi/webhook", content=body.encode(),
                               headers={"Content-Type": "application/json"})
        assert response.status_code == 401
        assert client.get(f"/api/orders/{order_id}/payment",
                          headers=headers).json()["order_state"] == OrderState.AWAITING_PAYMENT

    def test_a_webhook_signed_with_the_wrong_secret_is_refused(self, client, confirmed):
        headers, order_id, _ = confirmed
        payment = client.post(f"/api/orders/{order_id}/pay", headers=headers).json()
        external = _external(client, payment["id"])
        assert webhook(client, external, secret="подобранный").status_code == 401

    def test_a_tampered_body_breaks_the_signature(self, client, confirmed):
        """Подпись считается по сырому телу: правка суммы её ломает."""
        headers, order_id, _ = confirmed
        payment = client.post(f"/api/orders/{order_id}/pay", headers=headers).json()
        external = _external(client, payment["id"])

        body = json.dumps({"event": "payment.success", "paymentId": external, "amount": 60}).encode()
        signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        tampered = body.replace(b'"amount": 60', b'"amount": 10')
        response = client.post("/api/payments/kaspi/webhook", content=tampered,
                               headers={"X-Webhook-Signature": signature})
        assert response.status_code == 401

    def test_a_repeated_webhook_changes_nothing(self, client, confirmed):
        """Сервис доставляет до трёх раз."""
        headers, order_id, _ = confirmed
        payment = client.post(f"/api/orders/{order_id}/pay", headers=headers).json()
        external = _external(client, payment["id"])

        assert webhook(client, external).json().get("ok") is True
        assert webhook(client, external).json().get("duplicate") is True
        assert client.get(f"/api/orders/{order_id}/payment",
                          headers=headers).json()["order_state"] == OrderState.PAID

    def test_a_webhook_about_an_unknown_operation_is_ignored(self, client):
        assert webhook(client, "нет-такой-операции").json().get("ignored") is True

    def test_a_failed_payment_leaves_the_order_payable(self, client, confirmed):
        """У человека сел телефон — это не повод собирать заказ заново."""
        headers, order_id, _ = confirmed
        payment = client.post(f"/api/orders/{order_id}/pay", headers=headers).json()
        external = _external(client, payment["id"])

        webhook(client, external, event="payment.failed")
        state = client.get(f"/api/orders/{order_id}/payment", headers=headers).json()
        assert state["order_state"] == OrderState.AWAITING_PAYMENT
        assert client.post(f"/api/orders/{order_id}/pay", headers=headers).status_code == 200


class TestAmountMismatch:
    def test_a_different_amount_does_not_pay_the_order(self, client, confirmed):
        """Заказ на 60, подтверждение на 10 — это не оплата."""
        headers, order_id, _ = confirmed
        payment = client.post(f"/api/orders/{order_id}/pay", headers=headers).json()
        external = _external(client, payment["id"])

        webhook(client, external, amount=10)
        state = client.get(f"/api/orders/{order_id}/payment", headers=headers).json()
        assert state["order_state"] == OrderState.AWAITING_PAYMENT

    def test_the_mismatch_is_recorded_for_a_human_to_look_at(self, client, confirmed):
        """И не отказ тоже: деньги могли уйти. Это разбирают руками."""
        headers, order_id, _ = confirmed
        payment = client.post(f"/api/orders/{order_id}/pay", headers=headers).json()
        external = _external(client, payment["id"])

        webhook(client, external, amount=10)
        with db.session_scope() as session:
            from printhub.models import Payment
            row = session.get(Payment, payment["id"])
            assert row.state == PaymentState.MISMATCH
            assert "10" in row.problem and "60" in row.problem


class TestRefund:
    def test_the_admin_returns_the_money(self, client, confirmed):
        """Понадобится, когда принтер зажуёт лист после оплаты."""
        headers, order_id, _ = confirmed
        payment = client.post(f"/api/orders/{order_id}/pay", headers=headers).json()
        webhook(client, _external(client, payment["id"]))

        response = client.post(f"/api/admin/orders/{order_id}/refund",
                               headers=login(client, ADMIN, "Админ"),
                               json={"reason": "Принтер не напечатал"})
        assert response.status_code == 200
        assert response.json()["state"] == PaymentState.REFUNDED
        assert client.get(f"/api/orders/{order_id}",
                          headers=headers).json()["state"] == OrderState.REFUNDED

    def test_an_unpaid_order_cannot_be_refunded(self, client, confirmed):
        _, order_id, _ = confirmed
        response = client.post(f"/api/admin/orders/{order_id}/refund",
                               headers=login(client, ADMIN, "Админ"), json={})
        assert response.status_code == 409

    def test_an_ordinary_user_cannot_refund_anything(self, client, confirmed):
        headers, order_id, _ = confirmed
        assert client.post(f"/api/admin/orders/{order_id}/refund",
                           headers=headers, json={}).status_code == 404


class TestMerchantAccess:
    def test_the_credentials_never_come_back_out(self, client):
        """Доступ к кассе — секрет. Наружу только признак, что он задан."""
        admin = login(client, ADMIN, "Админ")
        client.put("/api/admin/merchant", headers=admin,
                   json={"token_sn": "СЕКРЕТ-SN", "vtoken_secret": "СЕКРЕТ-VT",
                         "profile_id": "p1"})
        body = client.get("/api/admin/merchant", headers=admin).text
        assert "СЕКРЕТ-SN" not in body and "СЕКРЕТ-VT" not in body
        assert json.loads(body)["merchant"]["configured"] is True

    def test_an_ordinary_user_cannot_read_or_set_them(self, client):
        headers = login(client, 42)
        assert client.get("/api/admin/merchant", headers=headers).status_code == 404
        assert client.put("/api/admin/merchant", headers=headers,
                          json={"token_sn": "x", "vtoken_secret": "y"}).status_code == 404


class TestProductionSafety:
    def test_fake_payments_cannot_exist_on_a_production_server(self, settings):
        """Иначе печать была бы бесплатной: «оплата» нажимается кнопкой."""
        settings.env = "prod"
        with pytest.raises(RuntimeError, match="PRINTHUB_DEV_LOGIN"):
            settings.validate()

    def test_production_refuses_to_start_without_a_webhook_secret(self, settings):
        """Без общего секрета подтверждение об оплате подделает любой,
        кто знает адрес."""
        settings.env = "prod"
        settings.dev_login = False
        settings.kaspi_webhook_secret = ""
        with pytest.raises(RuntimeError, match="WEBHOOK_SECRET"):
            settings.validate()


def _external(client, payment_id):
    """Номер операции у платёжной стороны — по нему приходит подтверждение."""
    with db.session_scope() as session:
        from printhub.models import Payment
        return session.get(Payment, payment_id).external_id
