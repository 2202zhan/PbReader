"""
Настройки. Всё имеет разумное значение по умолчанию — конфиг не обязателен.

Порядок приоритета: аргументы командной строки → переменные окружения →
pbreader.json рядом с программой → значения по умолчанию. Пути к сторонним
просмотрщикам здесь не нужны: библиотека печатает сама, искать по реестру
чужой .exe больше не требуется.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "pbreader.json"
ENV_PREFIX = "PBREADER_"


def _default_work_dir() -> Path:
    return Path(tempfile.gettempdir()) / "pbreader"


@dataclass
class Config:
    #: Куда складываются скачанные и сконвертированные файлы.
    work_dir: Path = field(default_factory=_default_work_dir)
    #: Принтер по умолчанию. Пусто — принтер по умолчанию из системы.
    printer: str = ""
    host: str = "127.0.0.1"
    port: int = 8756
    #: Токен доступа к локальному сервису. Пусто — сгенерируется при запуске.
    token: str = ""
    log_file: Path | None = None
    #: Сколько держать открытые задания предпросмотра, секунды.
    job_ttl_seconds: int = 1800
    #: Сколько хранить файлы в рабочем каталоге, часы. Аппарат работает
    #: месяцами: без уборки диск заканчивается.
    work_file_ttl_hours: int = 24
    #: Как часто убирать рабочий каталог, минуты.
    sweep_interval_minutes: int = 60

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        config = cls()
        config_path = Path(path) if path else Path(__file__).resolve().parent.parent / CONFIG_FILENAME
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                config = config.merged(data)
                logger.info("Настройки прочитаны: %s", config_path)
            except (OSError, ValueError) as exc:
                logger.warning("Не удалось прочитать %s (%s) — берутся значения по умолчанию", config_path, exc)

        env = {
            key[len(ENV_PREFIX):].lower(): value
            for key, value in os.environ.items()
            if key.startswith(ENV_PREFIX)
        }
        return config.merged(env)

    def merged(self, data: dict) -> "Config":
        merged = Config(**self.__dict__)
        for key, value in data.items():
            if value in (None, "") or not hasattr(merged, key):
                continue
            current = getattr(merged, key)
            if isinstance(current, Path) or (current is None and key == "log_file"):
                value = Path(value)
            elif isinstance(current, int) and not isinstance(current, bool):
                value = int(value)
            setattr(merged, key, value)
        return merged

    def ensure_work_dir(self) -> Path:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        return self.work_dir
