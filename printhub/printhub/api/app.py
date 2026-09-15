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
import logging
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import db, sessions, storage
from ..config import SESSION_TTL_SECONDS, Settings
from ..models import File as FileRow
from ..models import User, as_utc, now
from ..telegram.initdata import InitDataError, TelegramUser, validate

logger = logging.getLogger("printhub")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class TelegramLogin(BaseModel):
    init_data: str


class DevLogin(BaseModel):
    user_id: int = 1
    first_name: str = "Тестовый"
    username: str = "tester"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    settings.ensure_dirs()
    db.init(settings.database_url)

    app = FastAPI(title="PrintHub", version="0.1.0")
    app.state.settings = settings

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
        return {**as_profile(user), "files_count": int(count or 0)}

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
