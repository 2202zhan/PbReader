"""
Заказы: сборка, пересчёт и заморозка.

Главное правило — цена считается здесь, из тарифа в базе, и никогда из того,
что прислал клиент. Веб-апп работает на чужом телефоне, его код видно и можно
править; «итого 1 ₸» подделывается за минуту.

Второе правило — подтверждённый заказ не пересчитывается. Тариф меняется
админом когда угодно, но заказ, который человек уже подтвердил, обязан стоить
столько, сколько ему показали. Поэтому сумма и расшифровка сохраняются снимком.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select

from . import jobs
from .models import File as FileRow
from .models import Order, OrderState, Printer, TariffRow, now
from .pricing import Tariff, quote


class OrderProblem(ValueError):
    """То, что нужно объяснить человеку, а не записать в журнал и промолчать."""

    def __init__(self, message: str, reason: str = "bad_request") -> None:
        super().__init__(message)
        self.reason = reason


def tariff_for(session, printer_id: str | None) -> Tariff:
    """Прайс точки, а если своего нет — общий.

    Общий нужен, чтобы новый аппарат начинал продавать сразу, а не после того,
    как кто-то вспомнит завести ему цены.
    """
    row = None
    if printer_id:
        row = session.scalar(select(TariffRow).where(TariffRow.printer_id == printer_id))
    if row is None:
        row = session.scalar(select(TariffRow).where(TariffRow.printer_id.is_(None)))
    if row is None:
        # Базы без общего тарифа быть не должно — он заводится при первом
        # запуске. Но падать посреди заказа из-за этого тоже нельзя.
        return Tariff()
    return Tariff(
        price_mono=row.price_mono,
        price_color=row.price_color,
        duplex_discount=row.duplex_discount,
        heavy_ink_from=row.heavy_ink_from,
        heavy_extra_mono=row.heavy_extra_mono,
        heavy_extra_color=row.heavy_extra_color,
        min_order=row.min_order,
        currency=row.currency,
    )


def recalculate(session, order: Order, files_dir: Path) -> None:
    """Пересчитывает раскладку и цену по текущим параметрам заказа."""
    from pbreader.document import PdfDocument, PdfPasswordRequired

    file_row = session.get(FileRow, order.file_id)
    if file_row is None or file_row.deleted:
        raise OrderProblem("Файл заказа больше недоступен", "file_gone")

    if file_row.format_key != "pdf":
        # Остальные форматы конвертируются перед печатью, а конвертация живёт
        # на машине у принтера (там Office), не на сервере. Показать
        # предпросмотр и честно посчитать цену мы пока не можем — и говорим
        # это прямо, а не показываем пустой лист.
        raise OrderProblem(
            f"Пока можно заказывать только PDF. «{file_row.original_name}» — "
            f"{file_row.format_title}; поддержка появится вместе с печатью.",
            "format_not_ready",
        )

    printer = session.get(Printer, order.printer_id) if order.printer_id else None
    job = jobs.build_job(order.options or {}, printer)
    device = jobs.device_for(job, printer)

    path = files_dir / file_row.stored_name
    if not path.is_file():
        raise OrderProblem("Файл заказа больше недоступен", "file_gone")

    try:
        with PdfDocument(path) as document:
            plan = jobs.analyse(document, job, device)
    except PdfPasswordRequired:
        raise OrderProblem(
            "Этот PDF защищён паролем. Снимите защиту и загрузите файл заново.",
            "password_required",
        ) from None

    tariff = tariff_for(session, order.printer_id)
    price = quote(
        plan.side_coverages,
        color=job.color.value == "color",
        tariff=tariff,
        duplex_sides=plan.duplex_sides,
    )

    order.options = job.to_dict()
    order.plan = plan.to_dict()
    order.price = price.to_dict()
    order.amount = price.amount
    order.currency = price.currency
    order.updated_at = now()


def create(session, user_id: int, file_id: str, printer_id: str | None, options: dict,
           files_dir: Path) -> Order:
    file_row = session.scalar(
        select(FileRow).where(
            FileRow.id == file_id, FileRow.owner_id == user_id, FileRow.deleted.is_(False)
        )
    )
    if file_row is None:
        # Файл чужой или его нет — разница наружу не выносится.
        raise OrderProblem("Файл не найден", "not_found")

    order = Order(
        id=str(uuid.uuid4()),
        user_id=user_id,
        file_id=file_id,
        printer_id=printer_id,
        state=OrderState.DRAFT,
        options=options or {},
        created_at=now(),
        updated_at=now(),
    )
    recalculate(session, order, files_dir)
    session.add(order)
    return order


def confirm(session, order: Order, files_dir: Path) -> None:
    """Замораживает заказ: пересчёт напоследок — и цена больше не меняется."""
    if order.state != OrderState.DRAFT:
        raise OrderProblem("Этот заказ уже подтверждён", "not_editable")
    if not order.printer_id:
        raise OrderProblem("Не выбран принтер", "no_printer")

    # Последний пересчёт именно здесь: между созданием черновика и
    # подтверждением тариф мог измениться, и брать деньги надо по тому, что
    # человек видит сейчас, а не по тому, что видел полчаса назад.
    recalculate(session, order, files_dir)

    if order.amount <= 0:
        raise OrderProblem("Нечего печатать", "empty_job")

    order.state = OrderState.AWAITING_PAYMENT
    order.confirmed_at = now()
