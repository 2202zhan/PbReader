"""
Деньги: выставить счёт, принять подтверждение, вернуть.

Состояние платежа меняется только здесь. Раскидать это по обработчикам значило
бы получить несколько мест, где заказ становится оплаченным, — а достаточно
одного пропущенного условия в любом из них, чтобы печатать бесплатно.

Три правила, которые здесь закреплены.

Оплату подтверждает платёжная сторона, а не клиент. «Я оплатил» из веб-аппа —
это просто текст с чужого телефона.

Повтор ничего не меняет. Сервис Kaspi доставляет подтверждение до трёх раз, и
второй заход не должен ни закрывать заказ заново, ни отправлять второй возврат.

Сумма сверяется. Если в подтверждении не та сумма, что в заказе, это не оплата
и не отказ — это повод разобраться руками. Молча принять меньшие деньги нельзя,
молча отказать в принятых — тоже.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid

from sqlalchemy import select

from .models import Order, OrderState, Payment, PaymentState, WebhookEvent, now
from .payments import Intent, PaymentError, PaymentProvider

logger = logging.getLogger("printhub.billing")


class BillingProblem(ValueError):
    pass


def verify_signature(raw_body: bytes, header: str, secret: str) -> bool:
    """Проверяет подпись вебхука.

    Подпись считается по СЫРОМУ телу запроса. Разобрать JSON и собрать обратно
    нельзя: порядок ключей и пробелы изменятся, подпись перестанет сходиться —
    и, что хуже, попытка «починить» это сравнением по разобранным данным
    открыла бы дорогу подделке.
    """
    if not secret or not header:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    given = header[7:] if header.startswith("sha256=") else header
    return hmac.compare_digest(expected, given)


def pending_payment(session, order_id: str) -> Payment | None:
    return session.scalar(
        select(Payment).where(
            Payment.order_id == order_id, Payment.state == PaymentState.PENDING
        )
    )


def paid_payment(session, order_id: str) -> Payment | None:
    return session.scalar(
        select(Payment).where(Payment.order_id == order_id, Payment.state == PaymentState.PAID)
    )


def start(session, order: Order, provider: PaymentProvider) -> Payment:
    """Выставляет счёт на заказ."""
    if order.state == OrderState.PAID:
        raise BillingProblem("Этот заказ уже оплачен")
    if order.state != OrderState.AWAITING_PAYMENT:
        raise BillingProblem("Заказ не готов к оплате")
    if order.amount <= 0:
        raise BillingProblem("Нечего оплачивать")

    # Незакрытый счёт переиспользуем. Иначе на один заказ выпускалось бы
    # несколько QR, и человек, вернувшийся к старому экрану, заплатил бы
    # дважды за одно и то же.
    existing = pending_payment(session, order.id)
    if existing is not None and existing.amount == order.amount:
        return existing

    try:
        intent: Intent = provider.create(
            amount=order.amount,
            order_id=order.id,
            comment=f"PrintHub, заказ {order.id[:8]}",
        )
    except PaymentError as exc:
        raise BillingProblem(str(exc)) from None

    if existing is not None:
        # Сумма изменилась — старый счёт больше не годится.
        existing.state = PaymentState.EXPIRED
        existing.problem = "Сумма заказа изменилась"
        existing.updated_at = now()
        session.add(existing)

    payment = Payment(
        id=str(uuid.uuid4()),
        order_id=order.id,
        provider=provider.name,
        external_id=intent.external_id,
        amount=order.amount,
        currency=order.currency,
        state=PaymentState.PENDING,
        pay_url=intent.pay_url,
        created_at=now(),
        updated_at=now(),
    )
    session.add(payment)
    return payment


def apply(session, payment: Payment, state: str, *, receipt_url: str = "",
          reported_amount: int | None = None) -> Payment:
    """Единственное место, где платёж меняет состояние."""
    if payment.state in PaymentState.FINAL:
        # Повтор доставки или гонка двух проверок — ничего не делаем.
        logger.info("Платёж %s уже в состоянии %s, повтор пропущен", payment.id, payment.state)
        return payment

    order = session.get(Order, payment.order_id)

    if state == "paid":
        if reported_amount is not None and reported_amount != payment.amount:
            # Ни оплатой, ни отказом это считать нельзя.
            payment.state = PaymentState.MISMATCH
            payment.problem = (
                f"В подтверждении {reported_amount} {payment.currency}, "
                f"а в заказе {payment.amount}"
            )
            payment.updated_at = now()
            session.add(payment)
            logger.error("Расхождение суммы по платежу %s: %s", payment.id, payment.problem)
            return payment

        payment.state = PaymentState.PAID
        payment.receipt_url = receipt_url or payment.receipt_url
        payment.paid_at = now()
        if order is not None and order.state == OrderState.AWAITING_PAYMENT:
            order.state = OrderState.PAID
            order.updated_at = now()
            session.add(order)

    elif state in ("failed", "expired"):
        payment.state = PaymentState.FAILED if state == "failed" else PaymentState.EXPIRED
        payment.problem = "Оплата не прошла" if state == "failed" else "Счёт просрочен"
        # Заказ остаётся ждущим оплаты: человек может попробовать снова, и
        # заставлять его собирать заказ заново было бы наказанием за то, что
        # у него сел телефон.

    payment.updated_at = now()
    session.add(payment)
    return payment


def remember_event(session, key: str, payload: dict) -> bool:
    """Запоминает подтверждение. False — такое уже было."""
    if session.get(WebhookEvent, key) is not None:
        return False
    session.add(WebhookEvent(id=key, payload=payload, received_at=now()))
    return True


def sync(session, payment: Payment, provider: PaymentProvider) -> Payment:
    """Спрашивает состояние у платёжной стороны.

    Вебхук — это сетевой запрос, и он может не дойти: сервис попробует трижды и
    сдастся. Без собственной проверки оплаченный заказ завис бы в «ждёт оплаты»
    навсегда, а деньги остались бы у нас. Поэтому каждый раз, когда приложение
    спрашивает состояние, мы заодно спрашиваем его у Kaspi.
    """
    if payment.state != PaymentState.PENDING or not payment.external_id:
        return payment
    try:
        status = provider.check(payment.external_id)
    except PaymentError as exc:
        logger.warning("Не удалось проверить платёж %s: %s", payment.id, exc)
        return payment
    if status.state == "pending":
        return payment
    return apply(
        session, payment, status.state,
        receipt_url=status.receipt_url, reported_amount=status.amount,
    )


def refund(session, order: Order, provider: PaymentProvider, reason: str = "") -> Payment:
    """Возвращает деньги за оплаченный заказ.

    Понадобится это в первую очередь не по просьбе человека, а само: когда
    принтер зажевал лист после оплаты. Заставлять человека писать в поддержку
    из-за нашей поломки — худшее, что можно сделать с доверием.
    """
    payment = paid_payment(session, order.id)
    if payment is None:
        raise BillingProblem("По этому заказу нет оплаты")

    try:
        provider.refund(payment.external_id or "", payment.amount)
    except PaymentError as exc:
        raise BillingProblem(f"Возврат не прошёл: {exc}") from None

    payment.state = PaymentState.REFUNDED
    payment.problem = reason or "Возврат"
    payment.updated_at = now()
    order.state = OrderState.REFUNDED
    order.updated_at = now()
    session.add(payment)
    session.add(order)
    return payment
