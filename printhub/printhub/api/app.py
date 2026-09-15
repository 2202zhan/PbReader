"""
HTTP-сторона PrintHub.

Правило, которое здесь держит всё остальное: личность берётся только из
заголовка Authorization, никогда из тела запроса. Ни один обработчик не
принимает «я пользователь такой-то» — номер владельца всегда приходит из
проверенного токена и подставляется в запрос к базе. Поэтому чужой файл не
открывается не потому, что где-то стоит проверка, а потому, что он не находится.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import billing, db, jobs, orders, payments, sessions, storage
from ..config import SESSION_TTL_SECONDS, Settings
from ..models import File as FileRow
from ..models import (
    MerchantSession,
    Order,
    OrderState,
    Payment,
    PaymentState,
    Printer,
    TariffRow,
    User,
    as_utc,
    now,
)
from ..telegram.initdata import InitDataError, TelegramUser, validate

logger = logging.getLogger("printhub")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class TelegramLogin(BaseModel):
    init_data: str


class DevLogin(BaseModel):
    user_id: int = 1
    first_name: str = "Тестовый"
    username: str = "tester"


class NewOrder(BaseModel):
    file_id: str
    printer_id: str | None = None
    options: dict = {}


class OrderOptions(BaseModel):
    options: dict = {}
    printer_id: str | None = None


class PrinterForm(BaseModel):
    id: str | None = None
    title: str = ""
    location: str = ""
    paper: str = "A4"
    color_supported: bool = False
    duplex_supported: bool = True
    is_active: bool = True


class MerchantForm(BaseModel):
    token_sn: str
    vtoken_secret: str
    profile_id: str = ""


class RefundForm(BaseModel):
    reason: str = ""


class TariffForm(BaseModel):
    price_mono: int
    price_color: int
    heavy_ink_from: float = 0.30
    heavy_extra_mono: int = 0
    heavy_extra_color: int = 0
    min_order: int = 0


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    settings.ensure_dirs()
    db.init(settings.database_url)

    app = FastAPI(title="PrintHub", version="0.1.0")
    app.state.settings = settings
    _bootstrap(settings)

    def merchant_credentials():
        with db.session_scope() as session:
            return session.get(MerchantSession, "kaspi")

    provider = payments.build_provider(settings, merchant_credentials)
    app.state.payment_provider = provider

    if settings.dev_login:
        logger.warning(
            "PRINTHUB_DEV_LOGIN включён: вход доступен без телеграма, кому угодно. "
            "Только для местной разработки."
        )

    # ─── кто пришёл ───

    def current_user(authorization: str = Header(default="")) -> User:
        prefix = "bearer "
        if not authorization.lower().startswith(prefix):
            raise HTTPException(401, "Нужен токен сессии")
        try:
            user_id = sessions.verify(authorization[len(prefix):].strip(), settings.secret_key)
        except sessions.SessionError as exc:
            raise HTTPException(401, str(exc)) from None

        with db.session_scope() as session:
            user = session.get(User, user_id)
            if user is None:
                raise HTTPException(401, "Пользователь не найден")
            user.last_seen_at = now()
            session.add(user)
            return user

    def remember(profile: TelegramUser) -> User:
        with db.session_scope() as session:
            user = session.get(User, profile.id)
            if user is None:
                user = User(id=profile.id, created_at=now())
            user.first_name = profile.first_name
            user.last_name = profile.last_name
            user.username = profile.username
            user.language_code = profile.language_code
            user.last_seen_at = now()
            session.add(user)
            session.flush()
            return user

    def as_profile(user: User) -> dict:
        return {
            "id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username,
            "created_at": as_utc(user.created_at),
        }

    # ─── вход ───

    @app.post("/api/auth/telegram")
    def login_telegram(body: TelegramLogin):
        try:
            data = validate(body.init_data, settings.bot_token)
        except InitDataError as exc:
            # Наружу — одно и то же сообщение на любую причину: разница между
            # «подпись не та» и «срок вышел» помогает только подбирающему.
            logger.warning("Отказ во входе: %s", exc)
            raise HTTPException(401, "Не удалось подтвердить личность") from None

        user = remember(data.user)
        return {
            "token": sessions.issue(user.id, settings.secret_key, SESSION_TTL_SECONDS),
            "user": as_profile(user),
            "start_param": data.start_param,
        }

    @app.post("/api/auth/dev")
    def login_dev(body: DevLogin):
        if not settings.dev_login:
            raise HTTPException(404, "Not found")
        user = remember(
            TelegramUser(id=body.user_id, first_name=body.first_name, username=body.username)
        )
        return {
            "token": sessions.issue(user.id, settings.secret_key, SESSION_TTL_SECONDS),
            "user": as_profile(user),
            "start_param": "",
        }

    @app.get("/api/me")
    def me(user: User = Depends(current_user)):
        with db.session_scope() as session:
            count = session.scalar(
                select(func.count())
                .select_from(FileRow)
                .where(FileRow.owner_id == user.id, FileRow.deleted.is_(False))
            )
        return {
            **as_profile(user),
            "files_count": int(count or 0),
            "is_admin": settings.is_admin(user.id),
        }

    # ─── файлы ───

    @app.get("/api/files")
    def list_files(user: User = Depends(current_user)):
        with db.session_scope() as session:
            rows = session.scalars(
                select(FileRow)
                .where(FileRow.owner_id == user.id, FileRow.deleted.is_(False))
                .order_by(FileRow.created_at.desc())
            ).all()
        return {"files": [row.to_dict() for row in rows]}

    @app.post("/api/files")
    def upload(upload: UploadFile = File(...), user: User = Depends(current_user)):
        name = storage.display_name(upload.filename or "")
        try:
            stored = storage.save(upload.file, settings.files_dir, settings.max_upload_bytes)
        except storage.UploadTooLarge as exc:
            raise HTTPException(413, str(exc)) from None
        except storage.EmptyUpload as exc:
            raise HTTPException(400, str(exc)) from None

        fmt = _detect(stored.path)
        if not fmt.supported:
            stored.path.unlink(missing_ok=True)
            raise HTTPException(
                415,
                f"Файл «{name}» — это {fmt.title}, такой формат напечатать нельзя.",
            )

        row = FileRow(
            id=str(uuid.uuid4()),
            owner_id=user.id,
            original_name=name,
            stored_name=stored.stored_name,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            format_key=fmt.key,
            format_title=fmt.title,
            page_count=_count_pages(stored.path, fmt.key),
            created_at=now(),
        )
        with db.session_scope() as session:
            session.add(row)
        return row.to_dict()

    def _own_file(file_id: str, user: User) -> FileRow:
        with db.session_scope() as session:
            row = session.scalar(
                select(FileRow).where(
                    FileRow.id == file_id,
                    FileRow.owner_id == user.id,
                    FileRow.deleted.is_(False),
                )
            )
        if row is None:
            # Именно 404, а не 403: 403 подтвердил бы, что такой файл есть.
            raise HTTPException(404, "Файл не найден")
        return row

    @app.get("/api/files/{file_id}")
    def file_info(file_id: str, user: User = Depends(current_user)):
        return _own_file(file_id, user).to_dict()

    @app.get("/api/files/{file_id}/content")
    def file_content(file_id: str, user: User = Depends(current_user)):
        row = _own_file(file_id, user)
        path = settings.files_dir / row.stored_name
        if not path.is_file():
            raise HTTPException(410, "Файл уже удалён с диска")
        return FileResponse(path, filename=row.original_name, media_type="application/octet-stream")

    @app.delete("/api/files/{file_id}")
    def delete_file(file_id: str, user: User = Depends(current_user)):
        row = _own_file(file_id, user)
        (settings.files_dir / row.stored_name).unlink(missing_ok=True)
        with db.session_scope() as session:
            stored = session.get(FileRow, row.id)
            stored.deleted = True
            stored.deleted_at = dt.datetime.now(dt.timezone.utc)
            session.add(stored)
        return {"deleted": file_id}

    # ─── принтеры ───

    @app.get("/api/printers")
    def printers(user: User = Depends(current_user)):
        with db.session_scope() as session:
            rows = session.scalars(
                select(Printer).where(Printer.is_active.is_(True)).order_by(Printer.title)
            ).all()
            tariffs = {row.id: orders.tariff_for(session, row.id) for row in rows}
        return {
            "printers": [
                {
                    **row.to_dict(),
                    "price_mono": tariffs[row.id].price_mono,
                    "price_color": tariffs[row.id].price_color,
                    "currency": tariffs[row.id].currency,
                }
                for row in rows
            ]
        }

    # ─── заказы ───

    def _own_order(order_id: str, user: User, session) -> Order:
        order = session.scalar(
            select(Order).where(Order.id == order_id, Order.user_id == user.id)
        )
        if order is None:
            raise HTTPException(404, "Заказ не найден")
        return order

    def _order_payload(session, order: Order) -> dict:
        file_row = session.get(FileRow, order.file_id)
        return order.to_dict(file_name=file_row.original_name if file_row else "")

    @app.post("/api/orders")
    def create_order(body: NewOrder, user: User = Depends(current_user)):
        with db.session_scope() as session:
            try:
                order = orders.create(
                    session, user.id, body.file_id, body.printer_id,
                    body.options, settings.files_dir,
                )
            except orders.OrderProblem as exc:
                raise HTTPException(404 if exc.reason == "not_found" else 409, str(exc)) from None
            session.flush()
            return _order_payload(session, order)

    @app.get("/api/orders")
    def list_orders(user: User = Depends(current_user)):
        with db.session_scope() as session:
            rows = session.scalars(
                select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc())
            ).all()
            return {"orders": [_order_payload(session, row) for row in rows]}

    @app.get("/api/orders/{order_id}")
    def order_info(order_id: str, user: User = Depends(current_user)):
        with db.session_scope() as session:
            return _order_payload(session, _own_order(order_id, user, session))

    @app.patch("/api/orders/{order_id}")
    def update_order(order_id: str, body: OrderOptions, user: User = Depends(current_user)):
        with db.session_scope() as session:
            order = _own_order(order_id, user, session)
            if order.state not in OrderState.EDITABLE:
                # Подтверждённый заказ — это уже обязательство с зафиксированной
                # ценой. Правка параметров задним числом её бы разошлась.
                raise HTTPException(409, "Заказ уже подтверждён, параметры не меняются")
            order.options = {**(order.options or {}), **body.options}
            if body.printer_id is not None:
                order.printer_id = body.printer_id
            try:
                orders.recalculate(session, order, settings.files_dir)
            except orders.OrderProblem as exc:
                raise HTTPException(409, str(exc)) from None
            session.add(order)
            session.flush()
            return _order_payload(session, order)

    @app.post("/api/orders/{order_id}/confirm")
    def confirm_order(order_id: str, user: User = Depends(current_user)):
        with db.session_scope() as session:
            order = _own_order(order_id, user, session)
            try:
                orders.confirm(session, order, settings.files_dir)
            except orders.OrderProblem as exc:
                raise HTTPException(409, str(exc)) from None
            session.add(order)
            session.flush()
            return _order_payload(session, order)

    @app.delete("/api/orders/{order_id}")
    def cancel_order(order_id: str, user: User = Depends(current_user)):
        with db.session_scope() as session:
            order = _own_order(order_id, user, session)
            if order.state not in (OrderState.DRAFT, OrderState.AWAITING_PAYMENT):
                raise HTTPException(409, "Этот заказ уже нельзя отменить")
            order.state = OrderState.CANCELLED
            session.add(order)
        return {"cancelled": order_id}

    @app.get("/api/orders/{order_id}/preview/{sheet}")
    def order_preview(
        order_id: str,
        sheet: int,
        side: str = "front",
        width: int = 720,
        user: User = Depends(current_user),
    ):
        """Картинка листа — то, что выйдет из принтера, а не то, как выглядит PDF."""
        from pbreader.document import PdfDocument

        # Ширина приходит из адреса, то есть снаружи. Без предела один запрос
        # на 50 000 пикселей занял бы память и процессор сервера целиком.
        width = max(120, min(int(width), 1600))
        if side not in ("front", "back"):
            raise HTTPException(400, "Сторона бывает front или back")

        with db.session_scope() as session:
            order = _own_order(order_id, user, session)
            file_row = session.get(FileRow, order.file_id)
            printer = session.get(Printer, order.printer_id) if order.printer_id else None
            if file_row is None or file_row.deleted:
                raise HTTPException(410, "Файл заказа удалён")
            job = jobs.build_job(order.options or {}, printer)
            device = jobs.device_for(job, printer)
            path = settings.files_dir / file_row.stored_name

        if not path.is_file():
            raise HTTPException(410, "Файл заказа удалён")

        try:
            with PdfDocument(path) as document:
                preview = jobs.render(document, job, device, sheet, side, width)
        except IndexError as exc:
            raise HTTPException(404, str(exc)) from None

        return Response(
            content=preview.png,
            media_type="image/png",
            headers={
                # Лист зависит только от параметров заказа, а они меняются
                # через PATCH — который отдаёт новый ответ. Держим недолго:
                # достаточно, чтобы пролистывание не дёргало сервер.
                "Cache-Control": "private, max-age=60",
                "X-Sheet-Page": str(preview.page if preview.page is not None else ""),
                "X-Sheet-Clipped": "1" if preview.ink_clipped else "0",
            },
        )

    # ─── админка ───

    def admin(user: User = Depends(current_user)) -> User:
        if not settings.is_admin(user.id):
            # 404, а не 403: существование панели незачем подтверждать.
            raise HTTPException(404, "Not found")
        return user

    @app.get("/api/admin/printers")
    def admin_printers(_: User = Depends(admin)):
        with db.session_scope() as session:
            rows = session.scalars(select(Printer).order_by(Printer.created_at)).all()
            return {
                "printers": [
                    {**row.to_dict(), "tariff": orders.tariff_for(session, row.id).__dict__}
                    for row in rows
                ]
            }

    @app.post("/api/admin/printers")
    def admin_create_printer(body: PrinterForm, _: User = Depends(admin)):
        code = (body.id or "").strip()
        if not code:
            raise HTTPException(400, "Нужен код принтера — он же попадёт в ссылку с QR")
        with db.session_scope() as session:
            if session.get(Printer, code) is not None:
                raise HTTPException(409, f"Принтер «{code}» уже заведён")
            row = Printer(
                id=code, title=body.title or code, location=body.location, paper=body.paper,
                color_supported=body.color_supported, duplex_supported=body.duplex_supported,
                is_active=body.is_active, created_at=now(),
            )
            session.add(row)
            session.flush()
            return row.to_dict()

    @app.patch("/api/admin/printers/{printer_id}")
    def admin_update_printer(printer_id: str, body: PrinterForm, _: User = Depends(admin)):
        with db.session_scope() as session:
            row = session.get(Printer, printer_id)
            if row is None:
                raise HTTPException(404, "Принтер не найден")
            row.title = body.title or row.title
            row.location = body.location
            row.paper = body.paper
            row.color_supported = body.color_supported
            row.duplex_supported = body.duplex_supported
            row.is_active = body.is_active
            session.add(row)
            session.flush()
            return row.to_dict()

    @app.get("/api/admin/tariffs")
    def admin_tariffs(_: User = Depends(admin)):
        with db.session_scope() as session:
            rows = session.scalars(select(TariffRow)).all()
            return {"tariffs": [row.to_dict() for row in rows]}

    @app.put("/api/admin/tariffs/{scope}")
    def admin_set_tariff(scope: str, body: TariffForm, user: User = Depends(admin)):
        """scope — код принтера или «default» для общего прайса."""
        if body.price_mono < 0 or body.price_color < 0 or body.min_order < 0:
            raise HTTPException(400, "Цена не может быть отрицательной")
        if not 0 < body.heavy_ink_from <= 1:
            raise HTTPException(400, "Порог заполнения задаётся долей от 0 до 1")

        printer_id = None if scope == "default" else scope
        with db.session_scope() as session:
            if printer_id and session.get(Printer, printer_id) is None:
                raise HTTPException(404, "Принтер не найден")
            row = session.scalar(
                select(TariffRow).where(
                    TariffRow.printer_id.is_(None) if printer_id is None
                    else TariffRow.printer_id == printer_id
                )
            )
            if row is None:
                row = TariffRow(printer_id=printer_id)
            row.price_mono = body.price_mono
            row.price_color = body.price_color
            row.heavy_ink_from = body.heavy_ink_from
            row.heavy_extra_mono = body.heavy_extra_mono
            row.heavy_extra_color = body.heavy_extra_color
            row.min_order = body.min_order
            row.updated_at = now()
            row.updated_by = user.id
            session.add(row)
            session.flush()
            return row.to_dict()

    @app.get("/api/admin/orders")
    def admin_orders(limit: int = 50, _: User = Depends(admin)):
        with db.session_scope() as session:
            rows = session.scalars(
                select(Order).order_by(Order.created_at.desc()).limit(max(1, min(limit, 200)))
            ).all()
            return {
                "orders": [
                    {**_order_payload(session, row), "user_id": row.user_id} for row in rows
                ]
            }

    # ─── оплата ───

    @app.post("/api/orders/{order_id}/pay")
    def pay(order_id: str, user: User = Depends(current_user)):
        """Выставляет счёт и отдаёт ссылку, открывающую приложение Kaspi."""
        with db.session_scope() as session:
            order = _own_order(order_id, user, session)
            try:
                payment = billing.start(session, order, provider)
            except billing.BillingProblem as exc:
                raise HTTPException(409, str(exc)) from None
            session.flush()
            return payment.to_dict()

    @app.get("/api/orders/{order_id}/payment")
    def payment_state(order_id: str, user: User = Depends(current_user)):
        """Состояние оплаты.

        Заодно спрашиваем платёжную сторону: подтверждение приходит вебхуком, а
        вебхук может не дойти. Без этого оплаченный заказ завис бы в «ждёт
        оплаты», а деньги остались бы у нас.
        """
        with db.session_scope() as session:
            order = _own_order(order_id, user, session)
            payment = billing.paid_payment(session, order.id) or billing.pending_payment(
                session, order.id
            )
            if payment is None:
                return {"payment": None, "order_state": order.state}
            payment = billing.sync(session, payment, provider)
            session.flush()
            session.refresh(order)
            return {"payment": payment.to_dict(), "order_state": order.state}

    @app.post("/api/payments/kaspi/webhook")
    async def kaspi_webhook(request: Request):
        """Подтверждение оплаты от сервиса Kaspi.

        Адрес открытый — иначе сервис до него не достучится, — поэтому всё
        доверие держится на подписи. Проверяем её по СЫРОМУ телу: разобрать
        JSON и собрать обратно нельзя, порядок ключей изменится.
        """
        raw = await request.body()
        signature = request.headers.get("X-Webhook-Signature", "")
        if not billing.verify_signature(raw, signature, settings.kaspi_webhook_secret):
            logger.warning("Вебхук с неверной подписью, отклонён")
            raise HTTPException(401, "Подпись не сошлась")

        try:
            body = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "Не JSON") from None

        event = str(body.get("event") or "")
        external_id = str(body.get("paymentId") or "")
        state = payments.STATE_BY_EVENT.get(event)
        if not external_id or state is None:
            # Неизвестное событие — отвечаем «принято», чтобы сервис не
            # повторял доставку бесконечно, но ничего не делаем.
            logger.info("Вебхук с неизвестным событием %r, пропущен", event)
            return {"ignored": True}

        with db.session_scope() as session:
            if not billing.remember_event(session, f"{external_id}:{event}", body):
                # Сервис доставляет до трёх раз. Повтор обязан ничего не менять.
                return {"duplicate": True}

            payment = session.scalar(select(Payment).where(Payment.external_id == external_id))
            if payment is None:
                logger.warning("Вебхук про неизвестную операцию %s", external_id)
                return {"ignored": True}

            billing.apply(
                session, payment, state,
                receipt_url=str(body.get("receiptUrl") or ""),
                reported_amount=_as_int(body.get("amount")),
            )
            session.flush()
            return {"ok": True, "state": payment.state}

    @app.post("/api/payments/dev/{external_id}/{result}")
    def dev_payment_result(external_id: str, result: str):
        """Оплата понарошку — кнопка вместо приложения Kaspi.

        Существует только вместе с PRINTHUB_DEV_LOGIN: на боевом сервере это
        была бы бесплатная печать для всех желающих.
        """
        if not settings.dev_login:
            raise HTTPException(404, "Not found")
        if result not in ("paid", "failed", "expired"):
            raise HTTPException(400, "Бывает paid, failed или expired")

        provider.mark(external_id, result)
        with db.session_scope() as session:
            payment = session.scalar(select(Payment).where(Payment.external_id == external_id))
            if payment is None:
                raise HTTPException(404, "Платёж не найден")
            billing.apply(session, payment, result, reported_amount=payment.amount)
            session.flush()
            return payment.to_dict()

    @app.post("/api/admin/orders/{order_id}/refund")
    def admin_refund(order_id: str, body: RefundForm, _: User = Depends(admin)):
        with db.session_scope() as session:
            order = session.get(Order, order_id)
            if order is None:
                raise HTTPException(404, "Заказ не найден")
            try:
                payment = billing.refund(session, order, provider, body.reason)
            except billing.BillingProblem as exc:
                raise HTTPException(409, str(exc)) from None
            session.flush()
            return payment.to_dict()

    @app.get("/api/admin/merchant")
    def admin_merchant(_: User = Depends(admin)):
        """Подключена ли касса. Сами значения наружу не отдаются никогда."""
        with db.session_scope() as session:
            row = session.get(MerchantSession, "kaspi")
            return {
                "merchant": row.to_dict() if row else {"provider": "kaspi", "configured": False},
                "provider": provider.name,
            }

    @app.put("/api/admin/merchant")
    def admin_set_merchant(body: MerchantForm, user: User = Depends(admin)):
        with db.session_scope() as session:
            row = session.get(MerchantSession, "kaspi") or MerchantSession(provider="kaspi")
            row.token_sn = body.token_sn.strip()
            row.vtoken_secret = body.vtoken_secret.strip()
            row.profile_id = body.profile_id.strip()
            row.updated_at = now()
            row.updated_by = user.id
            session.add(row)
            session.flush()
            return row.to_dict()

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "env": settings.env,
            "dev_login": settings.dev_login,
            "bot_configured": bool(settings.bot_token),
        }

    @app.exception_handler(HTTPException)
    def http_error(_request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


def _as_int(value) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _bootstrap(settings: Settings) -> None:
    """То, без чего первый запуск не работает.

    Общий тариф нужен всегда: без него первый же заказ считался бы по ценам,
    зашитым в код, и админ не смог бы на это повлиять. Демонстрационный принтер
    заводится только в разработке — выдуманная точка печати на боевом сервере
    означала бы заказы в никуда.
    """
    with db.session_scope() as session:
        if session.scalar(select(TariffRow).where(TariffRow.printer_id.is_(None))) is None:
            session.add(TariffRow(printer_id=None, updated_at=now()))
            logger.info("Заведён общий тариф по умолчанию — поправьте его в админке")

        if settings.dev_login and session.scalar(select(Printer)) is None:
            session.add(
                Printer(
                    id="test",
                    title="Тестовый принтер",
                    location="Для разработки",
                    paper="A4",
                    color_supported=True,
                    duplex_supported=True,
                    created_at=now(),
                )
            )
            logger.info("Заведён тестовый принтер (только потому, что включён PRINTHUB_DEV_LOGIN)")


def _detect(path: Path):
    from pbreader.formats import UNKNOWN, detect

    try:
        return detect(path)
    except Exception as exc:  # pragma: no cover - на всякий случай
        logger.warning("Формат не определился: %s", exc)
        return UNKNOWN


def _count_pages(path: Path, format_key: str) -> int | None:
    """Число страниц — только для PDF.

    Остальные форматы сначала конвертируются, а конвертация живёт на машине у
    принтера, не здесь. Врать числом, полученным иначе, нельзя: по нему потом
    считается цена.
    """
    if format_key != "pdf":
        return None
    try:
        from pbreader.document import PdfDocument

        with PdfDocument(path) as document:
            return document.page_count
    except Exception:
        # Защищённый паролем или битый PDF. Это не повод отказывать в загрузке:
        # пароль спросим на этапе параметров.
        return None
