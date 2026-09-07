"""
Локальный HTTP-сервис: предпросмотр и печать для интерфейса PrintBox.

Почему HTTP, а не base64 в stdout: интерфейс на Electron может показать лист
одним тегом `<img src="http://127.0.0.1:8756/sessions/<id>/preview/2?...">` —
без передачи мегабайтов картинок через JSON и без перезапуска процесса на
каждое изменение параметров. Переключил человек «цветную» на «ч/б» — меняется
только URL, картинка перерисовывается.

Сервис слушает ТОЛЬКО 127.0.0.1 и требует токен. Это не формальность: у сервиса
есть метод «напечатать» и метод «открыть файл по пути», и любая страница,
открытая в браузере на этом же компьютере, иначе могла бы ими воспользоваться.
Токен принимается и заголовком `Authorization: Bearer …`, и параметром `?token=`
— тег `<img>` заголовки задавать не умеет.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import urllib.parse
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import Config
from .document import PdfError, PdfPasswordRequired
from .job import PrintJob
from .preview import DEFAULT_PREVIEW_WIDTH
from .printers import IS_WINDOWS, PrinterUnavailable, describe, default_printer, list_printers
from .session import SessionExpired, SessionStore

logger = logging.getLogger(__name__)

_PREVIEW_PATH = re.compile(r"^/sessions/([0-9a-f]{32})/preview/(\d+)$")
_SESSION_PATH = re.compile(r"^/sessions/([0-9a-f]{32})$")
_PRINT_PATH = re.compile(r"^/sessions/([0-9a-f]{32})/print$")
MAX_BODY_BYTES = 1 * 1024 * 1024


class ServiceError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class PrintBoxService(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: Config) -> None:
        self.config = config
        self.token = config.token or secrets.token_urlsafe(24)
        self.sessions = SessionStore(ttl_seconds=config.job_ttl_seconds)
        super().__init__((config.host, config.port), _Handler)

    def server_close(self) -> None:
        self.sessions.close_all()
        super().server_close()

    @property
    def base_url(self) -> str:
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}"


class _Handler(BaseHTTPRequestHandler):
    server_version = "PbReader"
    protocol_version = "HTTP/1.1"

    # --- инфраструктура ----------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s %s", self.address_string(), format % args)

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Предпросмотр меняется при любом изменении параметров, а URL при этом
        # может совпасть — кэшировать нельзя, иначе человек увидит прошлый лист.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _fail(self, status: HTTPStatus, message: str) -> None:
        logger.warning("%s %s → %d: %s", self.command, self.path, status, message)
        self._json({"error": message}, status)

    def _authorize(self, query: dict[str, list[str]]) -> None:
        expected = self.server.token
        header = self.headers.get("Authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else (query.get("token") or [""])[0]
        if not secrets.compare_digest(supplied, expected):
            raise ServiceError(HTTPStatus.UNAUTHORIZED, "Неверный или отсутствующий токен доступа")

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ServiceError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Слишком большое тело запроса")
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ServiceError(HTTPStatus.BAD_REQUEST, f"Тело запроса — не JSON: {exc}") from exc

    # --- маршрутизация ------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(HTTPStatus.NO_CONTENT, b"", "text/plain")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/health" and method == "GET":
                return self._json({"status": "ok", "windows": IS_WINDOWS})

            self._authorize(query)

            if path == "/printers" and method == "GET":
                return self._printers()
            if path == "/sessions" and method == "POST":
                return self._create_session()

            match = _SESSION_PATH.match(path)
            if match and method == "GET":
                return self._json(self.server.sessions.get(match.group(1)).describe())
            if match and method == "PATCH":
                return self._update_session(match.group(1))
            if match and method == "DELETE":
                self.server.sessions.drop(match.group(1))
                return self._json({"closed": True})

            match = _PREVIEW_PATH.match(path)
            if match and method == "GET":
                return self._preview(match.group(1), int(match.group(2)), query)

            match = _PRINT_PATH.match(path)
            if match and method == "POST":
                return self._print(match.group(1))

            self._fail(HTTPStatus.NOT_FOUND, f"Нет такого метода: {method} {path}")
        except ServiceError as exc:
            self._fail(exc.status, exc.message)
        except SessionExpired as exc:
            self._fail(HTTPStatus.NOT_FOUND, str(exc))
        except PdfPasswordRequired as exc:
            self._fail(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
        except (PdfError, PrinterUnavailable, ValueError, IndexError, FileNotFoundError) as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # pragma: no cover
            logger.exception("Необработанная ошибка при %s %s", method, path)
            self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, f"Внутренняя ошибка: {exc}")

    # --- обработчики --------------------------------------------------------

    def _printers(self) -> None:
        if not IS_WINDOWS:
            return self._json({"printers": [], "default": None, "note": "Печать доступна только в Windows"})
        printers = []
        for info in list_printers():
            entry = info.to_dict()
            try:
                entry["capabilities"] = describe(info.name).to_dict()
            except Exception as exc:
                entry["capabilities"] = None
                entry["capabilities_error"] = str(exc)
            printers.append(entry)
        return self._json({"printers": printers, "default": default_printer()})

    def _create_session(self) -> None:
        body = self._body()
        source = body.get("file") or body.get("path") or body.get("file_url")
        if not source:
            raise ServiceError(HTTPStatus.BAD_REQUEST, "Не указан файл: ожидается поле 'file'")

        from .pipeline import prepare_document

        config: Config = self.server.config
        pdf_path = prepare_document(str(source), config, token=body.get("device_token", ""))
        job = PrintJob.from_dict(body)
        if not job.printer:
            job = job.with_(printer=config.printer or (default_printer() if IS_WINDOWS else "") or "")

        session = self.server.sessions.create(pdf_path, job, password=body.get("password"))
        data = session.describe()
        data["preview_url_template"] = (
            f"/sessions/{session.id}/preview/{{sheet}}?side=front&width={DEFAULT_PREVIEW_WIDTH}"
        )
        return self._json(data, HTTPStatus.CREATED)

    def _update_session(self, session_id: str) -> None:
        session = self.server.sessions.get(session_id)
        body = self._body()
        # Меняем поверх текущего задания, чтобы интерфейс мог прислать одно поле.
        merged = {**session.job.to_dict(), **body}
        session.update_job(PrintJob.from_dict(merged))
        return self._json(session.describe())

    def _preview(self, session_id: str, sheet_number: int, query: dict[str, list[str]]) -> None:
        session = self.server.sessions.get(session_id)
        side = (query.get("side") or ["front"])[0]
        width = int((query.get("width") or [DEFAULT_PREVIEW_WIDTH])[0])
        preview = session.preview(sheet_number, side, width)

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(preview.png)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        # Сведения о листе кладём в заголовок: интерфейсу они нужны вместе с
        # картинкой, а второй запрос ради них — лишний круг. ensure_ascii здесь
        # обязателен: заголовки HTTP — латиница, а предупреждения у нас русские.
        self.send_header("X-PbReader-Sheet", json.dumps(preview.meta(), ensure_ascii=True))
        self.send_header("Access-Control-Expose-Headers", "X-PbReader-Sheet")
        self.end_headers()
        self.wfile.write(preview.png)

    def _print(self, session_id: str) -> None:
        session = self.server.sessions.get(session_id)
        body = self._body()
        if body:
            session.update_job(PrintJob.from_dict({**session.job.to_dict(), **body}))
        result = session.print()
        return self._json({"printed": True, **asdict(result)})


def serve(config: Config | None = None) -> PrintBoxService:
    """Поднимает сервис и возвращает его. Блокировки нет — вызывающий решает сам."""
    config = config or Config.load()
    service = PrintBoxService(config)
    thread = threading.Thread(target=service.serve_forever, name="pbreader-http", daemon=True)
    thread.start()
    logger.info("Сервис предпросмотра слушает %s", service.base_url)
    return service
