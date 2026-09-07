"""
Получение исходного файла: локальный путь или скачивание по ссылке.

Перенесено из прежнего print.py почти без изменений — эта часть работала. Из
правок только две: проверка размера (иначе киоск можно занять одним большим
файлом) и запрет писать за пределы рабочего каталога.
"""

from __future__ import annotations

import logging
import os
import re
import time
import urllib.parse
import uuid
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
CHUNK_SIZE = 64 * 1024


class DownloadError(RuntimeError):
    pass


def sanitize_filename(filename: str) -> str:
    """Приводит имя файла к безопасному виду, сохраняя расширение."""
    try:
        filename = urllib.parse.unquote(filename, encoding="utf-8")
    except UnicodeDecodeError:
        logger.warning("Имя файла не декодируется как UTF-8: %r", filename)

    base, ext = os.path.splitext(os.path.basename(filename))
    safe_base = re.sub(r"[^\w\d-]", "_", base)[:100] or f"document_{uuid.uuid4().hex[:8]}"
    safe_ext = ext.lower() if ext.lower() in SUPPORTED_EXTENSIONS else ".pdf"
    return f"{safe_base}{safe_ext}"


def download(
    url: str,
    dest_dir: str | Path,
    token: str = "",
    max_retries: int = 5,
    timeout: int = 30,
    verify_tls: bool = True,
) -> Path:
    """Скачивает файл с повторами при сетевых сбоях.

    404 не повторяется: если файла нет, он не появится, а лишние попытки — это
    лишние секунды, которые человек стоит у аппарата.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    decoded_url = urllib.parse.unquote(url, encoding="utf-8")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Скачивание (%d/%d): %s", attempt, max_retries, url)
            response = requests.get(
                decoded_url, headers=headers, stream=True, timeout=timeout, verify=verify_tls
            )
            response.raise_for_status()

            name = os.path.basename(urllib.parse.urlparse(url).path)
            path = dest_dir / sanitize_filename(name or f"document_{uuid.uuid4().hex}.pdf")

            written = 0
            with open(path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        handle.close()
                        path.unlink(missing_ok=True)
                        raise DownloadError(
                            f"Файл больше допустимых {MAX_DOWNLOAD_BYTES // (1024 * 1024)} МБ"
                        )
                    handle.write(chunk)

            logger.info("Скачано %.1f МБ: %s", written / 1024 / 1024, path)
            return path

        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 404:
                raise DownloadError(f"Файл не найден (404): {url}") from exc
            last_error = exc
            logger.warning("HTTP %s, попытка %d/%d", status, attempt, max_retries)
        except requests.exceptions.RequestException as exc:
            last_error = exc
            logger.warning("Сетевая ошибка, попытка %d/%d: %s", attempt, max_retries, exc)

        if attempt < max_retries:
            delay = min(2**attempt, 30)
            logger.info("Пауза %d с перед следующей попыткой", delay)
            time.sleep(delay)

    raise DownloadError(f"Не удалось скачать файл за {max_retries} попыток: {last_error}")


def resolve_source(source: str, dest_dir: str | Path, token: str = "", verify_tls: bool = True) -> Path:
    """Принимает и локальный путь, и URL — возвращает путь к файлу на диске."""
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in ("http", "https"):
        return download(source, dest_dir, token, verify_tls=verify_tls)
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {source}")
    return path
