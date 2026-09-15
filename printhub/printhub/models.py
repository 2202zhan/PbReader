"""Таблицы. Пока две — люди и их файлы; заказы появятся в третьей фазе."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    #: Номер пользователя в телеграме — он же наш. Своей нумерации нет: другого
    #: способа войти сюда не существует, а два ключа на одну сущность — это
    #: рассинхрон, который однажды случится.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    last_name: Mapped[str] = mapped_column(String(128), default="")
    username: Mapped[str] = mapped_column(String(128), default="")
    language_code: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)

    files: Mapped[list["File"]] = relationship(back_populates="owner")


class File(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    #: Как файл называется у человека. Показываем, но на диск под этим именем
    #: не кладём: имя приходит снаружи и содержит что угодно, вплоть до «../».
    original_name: Mapped[str] = mapped_column(Text, default="")
    #: Как он называется на диске — имя, которое придумали мы.
    stored_name: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    format_key: Mapped[str] = mapped_column(String(32), default="")
    format_title: Mapped[str] = mapped_column(String(128), default="")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    #: Удаление мягкое: заказ, который на файл ссылается, должен помнить, что
    #: печатали, даже когда сам файл уже стёрт с диска.
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped[User] = relationship(back_populates="files")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.original_name,
            "size": self.size_bytes,
            "format": self.format_key,
            "format_title": self.format_title,
            "pages": self.page_count,
            "created_at": as_utc(self.created_at),
        }


def as_utc(moment: dt.datetime | None) -> str | None:
    """Время наружу — всегда с зоной.

    SQLite зону не хранит и возвращает naive-время, а браузер такое читает как
    местное. Из Алматы это сдвиг на шесть часов: файл, загруженный минуту назад,
    показывался бы загруженным вечером.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc).isoformat()


class Printer(Base):
    """Точка печати.

    Пока принтера нет, профиль заводится вручную из админки, а геометрия листа
    берётся типовая и помечается как неизмеренная. Когда у аппарата появится
    агент (фаза 5), он пришлёт настоящие поля и возможности — и предпросмотр
    станет точным без единой правки в интерфейсе.
    """

    __tablename__ = "printers"

    #: Короткий код, он же то, что попадёт в наклейку с QR: t.me/bot?startapp=<id>
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    title: Mapped[str] = mapped_column(String(128), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    paper: Mapped[str] = mapped_column(String(32), default="A4")
    color_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    duplex_supported: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Настоящие поля печати, снятые с драйвера. Пока их нет — None, и
    #: интерфейс обязан сказать, что поля пока приблизительные.
    geometry: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)

    @property
    def margins_measured(self) -> bool:
        return bool(self.geometry)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "location": self.location,
            "paper": self.paper,
            "color_supported": self.color_supported,
            "duplex_supported": self.duplex_supported,
            "is_active": self.is_active,
            "margins_measured": self.margins_measured,
        }


class TariffRow(Base):
    """Прайс. Меняется из админки, в коде цен нет.

    Тариф привязан к принтеру, а строка с printer_id = None — общая: точка без
    своего прайса работает по ней. Так новый аппарат начинает продавать сразу,
    а не после того, как кто-то вспомнит завести ему цены.
    """

    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    printer_id: Mapped[str | None] = mapped_column(
        ForeignKey("printers.id"), nullable=True, unique=True
    )
    price_mono: Mapped[int] = mapped_column(Integer, default=30)
    price_color: Mapped[int] = mapped_column(Integer, default=150)
    duplex_discount: Mapped[int] = mapped_column(Integer, default=0)
    heavy_ink_from: Mapped[float] = mapped_column(Float, default=0.30)
    heavy_extra_mono: Mapped[int] = mapped_column(Integer, default=30)
    heavy_extra_color: Mapped[int] = mapped_column(Integer, default=150)
    min_order: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="KZT")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    def to_dict(self) -> dict:
        return {
            "printer_id": self.printer_id,
            "price_mono": self.price_mono,
            "price_color": self.price_color,
            "duplex_discount": self.duplex_discount,
            "heavy_ink_from": self.heavy_ink_from,
            "heavy_extra_mono": self.heavy_extra_mono,
            "heavy_extra_color": self.heavy_extra_color,
            "min_order": self.min_order,
            "currency": self.currency,
            "updated_at": as_utc(self.updated_at),
        }


class OrderState:
    """Состояния заказа.

    Черновик правится сколько угодно; всё остальное — уже обязательства, и
    параметры там заморожены. Оплата, очередь и печать появятся в фазах 4 и 5,
    но названия заведены сейчас, чтобы переход не менял смысл существующих.
    """

    DRAFT = "draft"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    #: Человек у аппарата и нажал «печатать». Задание ждёт агента.
    QUEUED = "queued"
    PRINTING = "printing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"

    #: Состояния, в которых заказ ещё можно править.
    EDITABLE = {DRAFT}


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id"), index=True)
    printer_id: Mapped[str | None] = mapped_column(ForeignKey("printers.id"), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default=OrderState.DRAFT, index=True)

    #: Параметры печати в виде, который понимает PbReader.
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Что из этих параметров вышло: листы, стороны, заполнение. Снимок, а не
    #: ссылка на расчёт: файл могут удалить, а заказ обязан помнить, за что
    #: с человека взяли деньги.
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: Сумма и её расшифровка на момент подтверждения — по тому тарифу, что
    #: действовал тогда. Смена прайса не должна менять цену принятого заказа.
    price: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="KZT")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    file: Mapped[File] = relationship()

    def to_dict(self, file_name: str = "") -> dict:
        return {
            "id": self.id,
            "state": self.state,
            "file_id": self.file_id,
            "file_name": file_name,
            "printer_id": self.printer_id,
            "options": self.options or {},
            "plan": self.plan,
            "price": self.price,
            "amount": self.amount,
            "currency": self.currency,
            "editable": self.state in OrderState.EDITABLE,
            "created_at": as_utc(self.created_at),
        }


class PaymentState:
    PENDING = "pending"      # счёт выставлен, человек ещё не заплатил
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"
    REFUNDED = "refunded"
    #: Сумма в подтверждении не совпала с суммой заказа. Само по себе это не
    #: оплата и не отказ — это повод разобраться руками, а не списать молча.
    MISMATCH = "mismatch"

    FINAL = {PAID, FAILED, EXPIRED, REFUNDED}


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="kaspi")
    #: Номер операции у платёжной стороны. По нему приходит подтверждение,
    #: поэтому он уникален: два платежа с одним номером означали бы, что
    #: непонятно, какой заказ оплачен.
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="KZT")
    state: Mapped[str] = mapped_column(String(32), default=PaymentState.PENDING, index=True)
    #: Ссылка, открывающая приложение Kaspi на экране оплаты.
    pay_url: Mapped[str] = mapped_column(Text, default="")
    receipt_url: Mapped[str] = mapped_column(Text, default="")
    problem: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    paid_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "state": self.state,
            "amount": self.amount,
            "currency": self.currency,
            "pay_url": self.pay_url,
            "receipt_url": self.receipt_url,
            "problem": self.problem,
            "created_at": as_utc(self.created_at),
            "paid_at": as_utc(self.paid_at),
        }


class WebhookEvent(Base):
    """Уже обработанные подтверждения.

    Платёжная сторона повторяет доставку до трёх раз, и повтор обязан ничего не
    менять: иначе один платёж закрыл бы заказ дважды, а возврат ушёл бы дважды
    следом. Ключ — операция плюс событие: это ровно то, что повторяется.
    """

    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class MerchantSession(Base):
    """Доступ к кассе Kaspi.

    Сервис kaspi-pos-automation после входа по SMS отдаёт три значения, и
    дальше все запросы идут с ними. Живут они ограниченно, а продлеваются
    только новым SMS — то есть руками. Поэтому лежат в базе и меняются из
    админки: перезапускать сервер ради протухшей сессии никуда не годится.

    Это секреты. В ответах админки они не показываются — только признак, что
    заданы, и когда обновлялись.
    """

    __tablename__ = "merchant_sessions"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    token_sn: Mapped[str] = mapped_column(Text, default="")
    vtoken_secret: Mapped[str] = mapped_column(Text, default="")
    profile_id: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    @property
    def is_set(self) -> bool:
        return bool(self.token_sn and self.vtoken_secret)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "configured": self.is_set,
            "updated_at": as_utc(self.updated_at),
        }
