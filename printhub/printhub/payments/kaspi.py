"""
Kaspi через kaspi-pos-automation.

Тот сервис — отдельный процесс на Node рядом с нами. Мы не переписываем его и
не лезем внутрь: ходим по HTTP на его адрес, который наружу не смотрит.

Доступ кассы (tokenSN, vtokenSecret, profileId) хранится у нас в базе и
подставляется заголовками. Он протухает, и продлить его можно только новым
входом по SMS — то есть руками, из админки. Поэтому отказ по правам здесь
означает не «сломалось», а «сессия кассы кончилась», и говорить это надо
именно так: иначе искать причину будут в коде.
"""

from __future__ import annotations

import logging

import httpx

from .base import Intent, PaymentError, PaymentProvider, Status

logger = logging.getLogger("printhub.payments")

#: Что означают статусы Kaspi в наших терминах. Всё, чего здесь нет, — это
#: «ещё думаем»: считать неизвестный статус успехом нельзя ни при каких
#: обстоятельствах, а отказом — значит бросить оплаченный заказ.
STATE_BY_STATUS = {
    "Success": "paid",
    "Processed": "paid",
    "Completed": "paid",
    "Error": "failed",
    "Declined": "failed",
    "Cancelled": "failed",
    "Expired": "expired",
    "Lost": "expired",
}

#: Что означают события вебхука.
STATE_BY_EVENT = {
    "payment.success": "paid",
    "payment.failed": "failed",
    "payment.expired": "expired",
    "payment.lost": "expired",
}


class KaspiProvider(PaymentProvider):
    name = "kaspi"

    def __init__(self, base_url: str, credentials_loader) -> None:
        self.base_url = base_url.rstrip("/")
        # Доступ читается на каждый запрос, а не при запуске: админ меняет его
        # после входа по SMS, и перезапускать сервер ради этого нельзя.
        self._credentials = credentials_loader

    def _headers(self) -> dict[str, str]:
        credentials = self._credentials()
        if not credentials or not credentials.is_set:
            raise PaymentError(
                "Касса Kaspi не подключена: войдите по SMS и впишите доступ в админке"
            )
        return {
            "X-Token-SN": credentials.token_sn,
            "X-Vtoken-Secret": credentials.vtoken_secret,
            "X-Profile-Id": credentials.profile_id or "",
        }

    def _call(self, method: str, path: str, **kwargs):
        try:
            with httpx.Client(timeout=20) as client:
                response = client.request(
                    method, f"{self.base_url}{path}", headers=self._headers(), **kwargs
                )
        except httpx.HTTPError as exc:
            raise PaymentError(f"Сервис Kaspi недоступен: {exc}") from exc

        if response.status_code == 401:
            raise PaymentError(
                "Сессия кассы Kaspi истекла — нужен новый вход по SMS. "
                "Приём оплаты остановлен до обновления доступа в админке"
            )
        if response.status_code >= 400:
            raise PaymentError(f"Сервис Kaspi ответил {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            raise PaymentError("Сервис Kaspi вернул не JSON") from exc

    def create(self, *, amount: int, order_id: str, comment: str) -> Intent:
        data = self._call("POST", "/qr/create", json={"amount": amount}).get("Data") or {}
        external_id = data.get("QrOperationId")
        if not external_id:
            raise PaymentError("Kaspi не выдал номер операции")

        # QrToken сервис уже переписывает на https://pay.kaspi.kz/pay/... —
        # это универсальная ссылка, она открывает приложение Kaspi на экране
        # оплаты. Именно её нажимает человек в веб-аппе.
        return Intent(
            external_id=str(external_id),
            pay_url=data.get("QrToken") or "",
            amount=amount,
            raw=data,
        )

    def check(self, external_id: str) -> Status:
        data = self._call("GET", "/qr/status", params={"qrOperationId": external_id})
        payload = data.get("Data") or data
        status = str(payload.get("Status") or "")
        return Status(
            state=STATE_BY_STATUS.get(status, "pending"),
            receipt_url=payload.get("ReceiptUrl") or "",
            amount=_as_int(payload.get("Amount")),
            raw=payload,
        )

    def refund(self, external_id: str, amount: int) -> dict:
        return self._call(
            "POST", "/refund/create", json={"qrOperationId": external_id, "amount": amount}
        )


def _as_int(value) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None
