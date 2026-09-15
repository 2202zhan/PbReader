"""
Собственный токен сессии.

initData проверяется один раз, при входе, и обменивается на этот токен. Иначе
пришлось бы проверять подпись телеграма на каждом запросе — а она с фиксированной
отметкой времени, и через час работы веб-аппа всё начало бы отказывать.

Токен подписан нашим ключом и несёт в себе только номер пользователя и срок.
Ничего секретного внутри нет, но подделать его нельзя.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


class SessionError(ValueError):
    pass


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(user_id: int, secret_key: str, ttl_seconds: int) -> str:
    payload = f"{user_id}.{int(time.time()) + ttl_seconds}"
    digest = hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{_b64(payload.encode())}.{_b64(digest)}"


def verify(token: str, secret_key: str) -> int:
    """Возвращает номер пользователя или отказывает. Середины нет."""
    try:
        body, signature = token.split(".", 1)
        payload = _unb64(body).decode()
        given = _unb64(signature)
    except (ValueError, UnicodeDecodeError):
        raise SessionError("Токен сессии не разобрался") from None

    expected = hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, given):
        raise SessionError("Подпись токена сессии не сошлась")

    # Разбор и проверка срока разнесены намеренно. SessionError — это ValueError,
    # и в общем try отказ «срок истёк» перехватывался бы собственным except и
    # уезжал наружу как «токен испорчен»: сообщение врало бы на каждой
    # протухшей сессии, а разбираться пришлось бы по чужим жалобам.
    try:
        user_id, expires_at = (int(part) for part in payload.split(".", 1))
    except ValueError:
        raise SessionError("Содержимое токена сессии испорчено") from None

    if time.time() > expires_at:
        raise SessionError("Срок сессии истёк")
    return user_id
