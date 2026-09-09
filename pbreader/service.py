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

С флагом `--ui` тот же сервис отдаёт по адресу `/` собственное окно программы
(pbreader/ui/index.html) — чтобы PbReader можно было открыть и попробовать
отдельно, без основного проекта. Страница получает токен подстановкой при
выдаче, и ИМЕННО ПОЭТОМУ она единственная отдаётся без заголовков CORS: иначе
страница из интернета, открытая в браузере на этом же компьютере, могла бы её
прочитать и забрать токен. Остальные методы CORS отдают, но без токена они
бесполезны.
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
from .coverage import CoverageLimits, check_limits
from .housekeeping import Housekeeper
from .output import JobTracker
from .ui import render_ui_page
from .job import PrintJob
from .preview import DEFAULT_PREVIEW_WIDTH
from .printers import (
    IS_WINDOWS,
    PrinterUnavailable,
    default_printer,
    describe,
    list_printers,
    preflight,
    read_telemetry,
    resolve_snmp,
)
from .session import SessionExpired, SessionStore

logger = logging.getLogger(__name__)

_PREVIEW_PATH = re.compile(r"^/sessions/([0-9a-f]{32})/preview/(\d+)$")
_SESSION_PATH = re.compile(r"^/sessions/([0-9a-f]{32})$")
_PRINT_PATH = re.compile(r"^/sessions/([0-9a-f]{32})/print$")
_COVERAGE_PATH = re.compile(r"^/sessions/([0-9a-f]{32})/coverage$")
_JOB_PATH = re.compile(r"^/print-jobs/(\d+)$")
_JOB_CANCEL_PATH = re.compile(r"^/print-jobs/(\d+)/cancel$")
MAX_BODY_BYTES = 1 * 1024 * 1024
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
UPLOAD_CHUNK = 256 * 1024


class ServiceError(Exception):
    """Ошибка с машиночитаемым кодом.

    Код нужен интерфейсу: «нужен пароль» — это не поломка, а следующий шаг
    диалога, и отличать его от прочих отказов по тексту сообщения нельзя.
    """

    def __init__(self, status: HTTPStatus, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


class PrintBoxService(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: Config, with_ui: bool = False) -> None:
        self.config = config
        self.token = config.token or secrets.token_urlsafe(24)
        self.with_ui = with_ui
        self.sessions = SessionStore(ttl_seconds=config.job_ttl_seconds)
        self.jobs = JobTracker()
        self.housekeeper = Housekeeper(
            config.work_dir,
            max_age_hours=config.work_file_ttl_hours,
            interval_minutes=config.sweep_interval_minutes,
            # Файлы открытых сессий уборка не трогает: по ним прямо сейчас
            # показывают предпросмотр.
            keep_provider=self.sessions.open_paths,
        )
        super().__init__((config.host, config.port), _Handler)
        self.housekeeper.start()

    def server_close(self) -> None:
        self.housekeeper.stop()
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

    def _send(self, status: HTTPStatus, body: bytes, content_type: str, cors: bool = True) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Предпросмотр меняется при любом изменении параметров, а URL при этом
        # может совпасть — кэшировать нельзя, иначе человек увидит прошлый лист.
        self.send_header("Cache-Control", "no-store")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Filename")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _fail(self, status: HTTPStatus, message: str, code: str = "error") -> None:
        logger.warning("%s %s → %d: %s", self.command, self.path, status, message)
        self._json({"error": message, "code": code}, status)

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
                return self._json({"status": "ok", "windows": IS_WINDOWS, "ui": self.server.with_ui})
            if path in ("/", "/ui") and method == "GET":
                return self._ui_page()

            self._authorize(query)

            if path == "/upload" and method == "POST":
                return self._upload()

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

            match = _COVERAGE_PATH.match(path)
            if match and method == "GET":
                return self._coverage(match.group(1))

            match = _PRINT_PATH.match(path)
            if match and method == "POST":
                return self._print(match.group(1))

            if path == "/print-jobs" and method == "GET":
                return self._json({"jobs": [j.to_dict() for j in self.server.jobs.all()]})

            match = _JOB_PATH.match(path)
            if match and method == "GET":
                return self._json(self.server.jobs.status(int(match.group(1))).to_dict())

            match = _JOB_CANCEL_PATH.match(path)
            if match and method == "POST":
                job_id = int(match.group(1))
                cancelled = self.server.jobs.cancel(job_id)
                return self._json({"cancelled": cancelled, **self.server.jobs.status(job_id).to_dict()})

            if path == "/printer-state" and method == "GET":
                return self._printer_state(query)

            if path == "/telemetry" and method == "GET":
                return self._telemetry(query)

            if path == "/preflight" and method == "GET":
                return self._preflight(query)

            self._fail(HTTPStatus.NOT_FOUND, f"Нет такого метода: {method} {path}")
        except ServiceError as exc:
            self._fail(exc.status, exc.message, exc.code)
        except SessionExpired as exc:
            self._fail(HTTPStatus.NOT_FOUND, str(exc), "session_expired")
        except PdfPasswordRequired as exc:
            # Не поломка, а следующий шаг: окно спросит пароль и повторит.
            self._fail(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc), "password_required")
        except (PdfError, PrinterUnavailable, ValueError, IndexError, FileNotFoundError) as exc:
            self._fail(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # pragma: no cover
            logger.exception("Необработанная ошибка при %s %s", method, path)
            self._fail(HTTPStatus.INTERNAL_SERVER_ERROR, f"Внутренняя ошибка: {exc}")

    # --- обработчики --------------------------------------------------------

    def _ui_page(self) -> None:
        """Отдаёт окно программы. Единственный ответ без CORS — см. заголовок модуля."""
        if not self.server.with_ui:
            raise ServiceError(
                HTTPStatus.NOT_FOUND,
                "Интерфейс выключен. Запустите с флагом --ui или командой `pbreader ui`",
            )
        page = render_ui_page(self.server.token)
        self._send(HTTPStatus.OK, page, "text/html; charset=utf-8", cors=False)

    def _upload(self) -> None:
        """Приём файла со страницы: в браузере есть содержимое файла, но не его путь.

        Тело запроса — сам файл, имя приходит заголовком X-Filename (в
        процентном кодировании, потому что заголовки HTTP — латиница, а файлы у
        людей называются по-русски). Multipart тут не нужен: поле ровно одно, а
        разбор multipart из стандартной библиотеки — лишняя поверхность для
        ошибок на файле в сотни мегабайт.
        """
        from .sources import sanitize_filename

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ServiceError(HTTPStatus.BAD_REQUEST, "Пустой файл")
        if length > MAX_UPLOAD_BYTES:
            raise ServiceError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"Файл больше допустимых {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ",
            )

        raw_name = urllib.parse.unquote(self.headers.get("X-Filename", "") or "document.pdf")
        target = self.server.config.ensure_work_dir() / f"{secrets.token_hex(4)}-{sanitize_filename(raw_name)}"

        remaining = length
        with open(target, "wb") as handle:
            while remaining > 0:
                chunk = self.rfile.read(min(UPLOAD_CHUNK, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                remaining -= len(chunk)

        if remaining > 0:
            target.unlink(missing_ok=True)
            raise ServiceError(HTTPStatus.BAD_REQUEST, "Передача файла оборвалась")

        logger.info("Принят файл: %s (%.1f МБ)", target.name, length / 1024 / 1024)
        return self._json({"path": str(target), "name": raw_name, "size": length}, HTTPStatus.CREATED)

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

        session = self.server.sessions.create(pdf_path, job, password=body.get("password") or None)
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

        # Порог заполнения проверяем до отправки: после того как задание ушло в
        # аппарат, тонер уже потрачен.
        limits = CoverageLimits.from_config(self.server.config)
        if limits.active:
            verdict = check_limits(session.coverage(), limits)
            if not verdict.allowed:
                raise ServiceError(
                    HTTPStatus.FORBIDDEN,
                    "; ".join(verdict.violations) or "Задание превышает порог заполнения",
                    "coverage_limit",
                )

        result = session.print()

        # «Отправлено» — это ещё не «напечатано»: задание только встало в
        # очередь. Регистрируем его, чтобы интерфейс мог довести до конца и
        # показать замятие или конец бумаги, а не отчитаться об успехе.
        status = None
        if result.job_id:
            config = self.server.config
            target = self._snmp_target(result.printer)
            status = self.server.jobs.register(
                result.job_id,
                result.printer,
                result.pages_sent,
                session.document.path.name,
                # Счётчик механизма снимается прямо сейчас: после него ответ
                # «напечатано» перестанет быть догадкой.
                snmp_host=target.host,
                community=target.community,
                expected_sheets=result.sheets,
                snmp_timeout=config.snmp_timeout,
            ).to_dict()
            if not target.available:
                logger.info(
                    "Задание %d: подтвердить печать счётчиком аппарата нечем — %s",
                    result.job_id, target.reason,
                )

        return self._json({"submitted": True, "status": status, **asdict(result)})

    def _coverage(self, session_id: str) -> None:
        """Сколько тонера уйдёт на задание — и не выходит ли это за порог."""
        session = self.server.sessions.get(session_id)
        limits = CoverageLimits.from_config(self.server.config)
        coverage = session.coverage()
        return self._json({
            **coverage.to_dict(),
            "verdict": check_limits(coverage, limits).to_dict(),
            "limits": {
                "max_page_coverage": limits.max_page_coverage,
                "max_ink_units": limits.max_ink_units,
                "enforce": limits.enforce,
            },
        })

    def _snmp_target(self, printer: str):
        config = self.server.config
        return resolve_snmp(
            printer, host=config.snmp_host, community=config.snmp_community,
            enabled=config.snmp_enabled,
        )

    def _telemetry(self, query: dict[str, list[str]]) -> None:
        """Что аппарат рассказывает о себе: счётчик, бумага, тонер, неполадки."""
        config = self.server.config
        printer = (query.get("printer") or [config.printer or (default_printer() if IS_WINDOWS else "")])[0]
        if not printer:
            raise ServiceError(HTTPStatus.BAD_REQUEST, "Не указан принтер", "no_printer")
        target = self._snmp_target(printer)
        info = read_telemetry(
            printer, community=config.snmp_community, host=config.snmp_host,
            timeout=config.snmp_timeout, enabled=config.snmp_enabled,
        )
        return self._json({"printer": printer, "target": target.to_dict(), **info.to_dict()})

    def _preflight(self, query: dict[str, list[str]]) -> None:
        """Можно ли принимать оплату: готов ли аппарат, есть ли бумага и тонер."""
        config = self.server.config
        printer = (query.get("printer") or [config.printer or (default_printer() if IS_WINDOWS else "")])[0]
        if not printer:
            raise ServiceError(HTTPStatus.BAD_REQUEST, "Не указан принтер", "no_printer")
        tray_raw = (query.get("tray") or [""])[0]
        result = preflight(
            printer,
            tray=int(tray_raw) if tray_raw.strip().isdigit() else None,
            community=config.snmp_community,
            host=config.snmp_host if config.snmp_enabled else "",
            timeout=config.snmp_timeout,
        )
        return self._json(result.to_dict())

    def _printer_state(self, query: dict[str, list[str]]) -> None:
        """Готов ли принтер. Спросить это ДО оплаты дешевле, чем объясняться после."""
        printer = (query.get("printer") or [""])[0]
        if not printer:
            raise ServiceError(HTTPStatus.BAD_REQUEST, "Не указан принтер", "no_printer")
        if not IS_WINDOWS:
            return self._json({"printer": printer, "ready": False, "status": "только в Windows", "queued": 0})
        from .output import printer_state

        return self._json(printer_state(printer))


def serve(config: Config | None = None, with_ui: bool = False) -> PrintBoxService:
    """Поднимает сервис и возвращает его. Блокировки нет — вызывающий решает сам."""
    config = config or Config.load()
    service = PrintBoxService(config, with_ui=with_ui)
    thread = threading.Thread(target=service.serve_forever, name="pbreader-http", daemon=True)
    thread.start()
    logger.info("Сервис предпросмотра слушает %s", service.base_url)
    return service
