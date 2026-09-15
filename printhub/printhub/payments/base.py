"""
Приём денег — за интерфейсом.

Kaspi мы берём через kaspi-pos-automation, а это эмуляция мобильного клиента:
в ней прописаны версия приложения, модель телефона и версии сетевых библиотек,
и Kaspi эти значения проверяет. Любое обновление их приложения может разом
остановить приём платежей, а сессия кассы живёт ограниченно и продлевается
только новым SMS.

Поэтому провайдер отделён от заказов: когда дойдёт до официального Kaspi
Business, переехать надо будет заменой одного класса, а не переписыванием
заказов, возвратов и состояний.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class PaymentError(RuntimeError):
    """Не удалось выставить счёт или получить его состояние."""


@dataclass(frozen=True)
class Intent:
    """Выставленный счёт."""

    external_id: str
    #: Ссылка, открывающая приложение Kaspi сразу на экране оплаты.
    pay_url: str = ""
    amount: int = 0
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Status:
    """Состояние счёта по данным платёжной стороны."""

    state: str
    receipt_url: str = ""
    amount: int | None = None
    raw: dict = field(default_factory=dict)


class PaymentProvider:
    name = "base"

    def create(self, *, amount: int, order_id: str, comment: str) -> Intent:
        raise NotImplementedError

    def check(self, external_id: str) -> Status:
        """Спрашивает состояние у платёжной стороны.

        Нужен не для красоты: подтверждение приходит вебхуком, а вебхук — это
        сетевой запрос, который может не дойти. Без собственной проверки
        оплаченный заказ завис бы в «ждёт оплаты» навсегда.
        """
        raise NotImplementedError

    def refund(self, external_id: str, amount: int) -> dict:
        raise NotImplementedError
