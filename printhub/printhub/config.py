"""
Настройки. Всё из окружения, ничего из репозитория.

Токен бота — это и ключ к переписке, и ключ, которым проверяется личность
пользователя. Попав в git, он останется в истории навсегда, поэтому единственный
способ его задать — переменная окружения.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

#: Сколько живёт наш собственный токен сессии, выданный в обмен на initData.
SESSION_TTL_SECONDS = 7 * 24 * 3600

#: Предел размера загружаемого файла. Печать больших документов — обычное дело,
#: но без предела один запрос способен занять весь диск.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


def _ids(name: str) -> frozenset[int]:
    """Список номеров из переменной окружения.

    Мусор молча пропускается, но не молча для журнала: опечатка в номере
    админа иначе означала бы, что панель просто «не открывается», и искать
    причину пришлось бы наугад.
    """
    result = set()
    for part in os.environ.get(name, "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.add(int(part))
        except ValueError:
            logging.getLogger("printhub").warning(
                "%s: «%s» — это не номер пользователя, пропускаю", name, part
            )
    return frozenset(result)


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "да")


@dataclass
class Settings:
    env: str = "dev"
    bot_token: str = ""
    secret_key: str = ""
    data_dir: Path = field(default_factory=lambda: Path("data"))
    database_url: str = ""
    max_upload_bytes: int = MAX_UPLOAD_BYTES
    #: Вход без телеграма — чтобы разрабатывать в браузере, не поднимая туннель.
    dev_login: bool = False
    webapp_url: str = ""
    #: Кто видит админку. Номера в телеграме, через запятую.
    admin_ids: frozenset[int] = frozenset()
    #: Адрес сервиса kaspi-pos-automation. Он рядом и наружу не смотрит.
    kaspi_url: str = "http://127.0.0.1:3000"
    #: Общий секрет с этим сервисом: им подписан вебхук об оплате.
    kaspi_webhook_secret: str = ""

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    @property
    def is_production(self) -> bool:
        return self.env.lower() in ("prod", "production", "боевой")

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @classmethod
    def load(cls) -> "Settings":
        data_dir = Path(os.environ.get("PRINTHUB_DATA_DIR", "data")).expanduser()
        settings = cls(
            env=os.environ.get("PRINTHUB_ENV", "dev"),
            bot_token=os.environ.get("PRINTHUB_BOT_TOKEN", "").strip(),
            secret_key=os.environ.get("PRINTHUB_SECRET_KEY", "").strip(),
            data_dir=data_dir,
            database_url=os.environ.get(
                "PRINTHUB_DATABASE_URL", f"sqlite:///{(data_dir / 'printhub.db').as_posix()}"
            ),
            max_upload_bytes=int(os.environ.get("PRINTHUB_MAX_UPLOAD_BYTES", MAX_UPLOAD_BYTES)),
            dev_login=_flag("PRINTHUB_DEV_LOGIN"),
            webapp_url=os.environ.get("PRINTHUB_WEBAPP_URL", "").strip(),
            admin_ids=_ids("PRINTHUB_ADMIN_IDS"),
            kaspi_url=os.environ.get("PRINTHUB_KASPI_URL", "http://127.0.0.1:3000").strip(),
            kaspi_webhook_secret=os.environ.get("PRINTHUB_KASPI_WEBHOOK_SECRET", "").strip(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Проверяет то, что нельзя проверить позже, — до приёма первого запроса.

        Отдельно про dev_login: это вход вообще без телеграма, любым желающим,
        от имени любого пользователя. В разработке без него неудобно — нужен
        публичный HTTPS-туннель, — а на боевом сервере это дыра размером с
        дверь. Поэтому вместе с боевым окружением он не включается никогда, и
        спорить тут не о чем: процесс просто не поднимется.
        """
        if self.is_production:
            if self.dev_login:
                raise RuntimeError(
                    "PRINTHUB_DEV_LOGIN и PRINTHUB_ENV=prod вместе недопустимы: "
                    "это вход в чужой профиль без всякой проверки"
                )
            if not self.bot_token:
                raise RuntimeError("PRINTHUB_BOT_TOKEN обязателен на боевом сервере")
            if not self.secret_key:
                raise RuntimeError("PRINTHUB_SECRET_KEY обязателен на боевом сервере")
            if not self.kaspi_webhook_secret:
                # Без общего секрета подтверждение об оплате подделает кто
                # угодно, кто знает адрес: печать станет бесплатной.
                raise RuntimeError(
                    "PRINTHUB_KASPI_WEBHOOK_SECRET обязателен: без него подтверждение "
                    "об оплате нечем проверить"
                )

        if not self.secret_key:
            # В разработке ключ можно и выдумать — но новый на каждый запуск,
            # чтобы выданные сессии не пережили перезапуск незаметно для нас.
            self.secret_key = secrets.token_hex(32)

    def ensure_dirs(self) -> None:
        self.files_dir.mkdir(parents=True, exist_ok=True)
