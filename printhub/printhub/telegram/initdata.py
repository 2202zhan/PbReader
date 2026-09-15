"""
Кто открыл веб-апп.

Веб-апп — это код на телефоне пользователя. Его видно, его можно править, и всё
что оттуда приходит — включая «я пользователь номер такой-то» — само по себе не
значит ничего. Единственное доказательство личности здесь одно: телеграм
подписывает параметры запуска ключом, который выводится из токена бота.
Проверка этой подписи — та точка, где кончается доверие к клиенту.

Поэтому здесь нет ни одного места, где личность берётся из тела запроса.
Она берётся только из строки, подпись которой сошлась.

Алгоритм (документация Telegram, «Validating data received via the Mini App»):

    data_check_string = поля кроме hash, отсортированные по имени,
                        каждое как «ключ=значение», через перевод строки
    secret_key        = HMAC_SHA256(ключ="WebAppData", сообщение=токен бота)
    ожидаемый hash    = HMAC_SHA256(ключ=secret_key, сообщение=data_check_string)

Поле signature из строки НЕ выбрасывается: телеграм считает hash по всему, что
прислал, включая его. Выбрасывается только сам hash.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class InitDataError(ValueError):
    """Подпись не сошлась, протухла или строка не разобралась.

    Причина намеренно не раскладывается по подклассам: наружу пользователю
    уходит одно «не удалось подтвердить личность» в любом случае. Разница между
    «подпись не та» и «срок вышел» интересна только журналу.
    """


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    language_code: str = ""
    is_premium: bool = False
    photo_url: str = ""

    @property
    def display_name(self) -> str:
        full = " ".join(part for part in (self.first_name, self.last_name) if part)
        return full or self.username or f"id{self.id}"


@dataclass(frozen=True)
class InitData:
    user: TelegramUser
    auth_date: int
    #: Параметр из ссылки вида t.me/bot?startapp=ЗНАЧЕНИЕ. Сюда придёт номер
    #: аппарата, когда человек отсканирует наклейку на принтере.
    start_param: str = ""
    query_id: str = ""
    chat_type: str = ""


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def validate(init_data: str, bot_token: str, max_age_seconds: int = 3600) -> InitData:
    """Проверяет подпись и возвращает то, что в ней подписано.

    max_age_seconds отсекает переигранную строку: перехваченный initData иначе
    работал бы вечно. Час — с запасом на то, чтобы человек успел открыть
    веб-апп и нажать кнопку; дальше он обменивается на собственный токен сессии,
    и возраст initData больше ни на что не влияет.
    """
    if not bot_token:
        # Пустой токен дал бы валидный ключ и подпись, которая сходится с
        # чем угодно, что подписано пустым токеном. Это отказ, а не «ну ладно».
        raise InitDataError("Токен бота не задан — проверить подпись нечем")

    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    if not pairs:
        raise InitDataError("Пустые данные запуска")

    fields = dict(pairs)
    received_hash = fields.pop("hash", "")
    if not received_hash:
        raise InitDataError("В данных запуска нет подписи")

    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    expected = hmac.new(
        _secret_key(bot_token), check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    # Сравнение постоянного времени: обычное «==» выходит из цикла на первом
    # несовпавшем байте, и по времени ответа подпись можно подобрать побайтно.
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("Подпись данных запуска не сошлась")

    try:
        auth_date = int(fields.get("auth_date", ""))
    except ValueError:
        raise InitDataError("Нет отметки времени в данных запуска") from None

    age = time.time() - auth_date
    if max_age_seconds and age > max_age_seconds:
        raise InitDataError(f"Данные запуска устарели на {int(age - max_age_seconds)} с")
    if age < -300:
        # Время из будущего — либо часы врут, либо строку сочинили.
        raise InitDataError("Отметка времени из будущего")

    raw_user = fields.get("user", "")
    if not raw_user:
        raise InitDataError("В данных запуска нет пользователя")
    try:
        payload = json.loads(raw_user)
        user = TelegramUser(
            id=int(payload["id"]),
            first_name=str(payload.get("first_name", "")),
            last_name=str(payload.get("last_name", "")),
            username=str(payload.get("username", "")),
            language_code=str(payload.get("language_code", "")),
            is_premium=bool(payload.get("is_premium", False)),
            photo_url=str(payload.get("photo_url", "")),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise InitDataError(f"Пользователь в данных запуска не разобрался: {exc}") from None

    return InitData(
        user=user,
        auth_date=auth_date,
        start_param=fields.get("start_param", ""),
        query_id=fields.get("query_id", ""),
        chat_type=fields.get("chat_type", ""),
    )


def sign(fields: dict[str, str], bot_token: str) -> str:
    """Собирает подписанную строку — так же, как её собрал бы телеграм.

    Нужна тестам: без неё проверку подписи можно испытать только вручную,
    настоящим ботом и настоящим телефоном, то есть практически никогда.
    """
    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    digest = hmac.new(
        _secret_key(bot_token), check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    from urllib.parse import urlencode

    return urlencode({**fields, "hash": digest})
