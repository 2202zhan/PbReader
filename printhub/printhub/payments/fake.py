"""
Платежи понарошку — для разработки и тестов.

Настоящий Kaspi требует кассу, вход по SMS и живые деньги. Разрабатывать поверх
этого нельзя: каждый прогон тестов стоил бы реальных тенге, а половина путей
(отказ, просрочка, возврат) вообще не воспроизводится по желанию.

Этот провайдер включается только вместе с PRINTHUB_DEV_LOGIN, то есть там же,
где и вход без телеграма, — и на боевом сервере существовать не может.
"""

from __future__ import annotations

import uuid

from .base import Intent, PaymentProvider, Status


class FakeProvider(PaymentProvider):
    name = "fake"

    def __init__(self) -> None:
        self._states: dict[str, str] = {}

    def create(self, *, amount: int, order_id: str, comment: str) -> Intent:
        external_id = f"fake-{uuid.uuid4().hex[:12]}"
        self._states[external_id] = "pending"
        return Intent(
            external_id=external_id,
            # Ссылка ведёт внутрь самого приложения: в разработке «открыть
            # Kaspi» — это экран, где можно нажать «оплатил» или «отказ».
            pay_url=f"/dev-pay/{external_id}",
            amount=amount,
            raw={"fake": True, "order_id": order_id, "comment": comment},
        )

    def mark(self, external_id: str, state: str) -> None:
        self._states[external_id] = state

    def check(self, external_id: str) -> Status:
        return Status(state=self._states.get(external_id, "pending"), raw={"fake": True})

    def refund(self, external_id: str, amount: int) -> dict:
        self._states[external_id] = "refunded"
        return {"fake": True, "refunded": amount}
