"""
Путь файла до печатаемого PDF: получить → сконвертировать → открыть.

Вынесено отдельно, потому что этим пользуются оба входа — и разовый запуск из
командной строки, и сервис предпросмотра, — и делать это они обязаны одинаково.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import Config
from .convert import needs_conversion, to_pdf
from .sources import resolve_source

logger = logging.getLogger(__name__)


def prepare_document(source: str, config: Config, token: str = "", verify_tls: bool = True) -> Path:
    """Возвращает путь к PDF, готовому к печати.

    `source` — локальный путь или http(s)-ссылка. Офисные форматы
    конвертируются, PDF пропускается как есть.
    """
    work_dir = config.ensure_work_dir()
    path = resolve_source(source, work_dir, token, verify_tls=verify_tls)
    if needs_conversion(path):
        logger.info("Конвертация в PDF: %s", path.name)
        return to_pdf(path, work_dir)
    return path
