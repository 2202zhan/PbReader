"""
Бот: одна кнопка, открывающая веб-апп.

Библиотеки вроде aiogram здесь были бы лишним весом: весь бот — это ответ на
/start и настройка кнопки меню. Длинный опрос (long polling) выбран потому, что
вебхук требует публичного HTTPS, а его на этапе местной разработки нет.

Токен не печатается в журнал никогда — ни целиком, ни в ссылке на Bot API.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("printhub.bot")

API = "https://api.telegram.org"

GREETING = (
    "Привет! Это PrintHub — печать документов прямо из телеграма.\n\n"
    "Загрузите файл, выберите параметры, оплатите — и заберите распечатку у принтера.\n\n"
    "Нажмите кнопку ниже, чтобы открыть приложение."
)


class Bot:
    def __init__(self, token: str, webapp_url: str) -> None:
        if not token:
            raise ValueError("Токен бота не задан")
        self._token = token
        self.webapp_url = webapp_url
        self._client = httpx.Client(timeout=65)

    def _call(self, method: str, **payload):
        response = self._client.post(f"{API}/bot{self._token}/{method}", json=payload)
        data = response.json()
        if not data.get("ok"):
            # В сообщении об ошибке телеграма токена нет, а вот в URL он есть —
            # поэтому в журнал идёт только название метода.
            raise RuntimeError(f"Bot API {method}: {data.get('description', 'неизвестная ошибка')}")
        return data["result"]

    def whoami(self) -> dict:
        return self._call("getMe")

    def set_menu_button(self) -> None:
        """Кнопка «Открыть» в поле ввода — главный вход в веб-апп."""
        if not self.webapp_url:
            logger.warning("PRINTHUB_WEBAPP_URL не задан — кнопка меню не настроена")
            return
        self._call(
            "setChatMenuButton",
            menu_button={
                "type": "web_app",
                "text": "Печать",
                "web_app": {"url": self.webapp_url},
            },
        )

    def _reply(self, chat_id: int) -> None:
        markup = None
        if self.webapp_url:
            markup = {
                "inline_keyboard": [
                    [{"text": "Открыть PrintHub", "web_app": {"url": self.webapp_url}}]
                ]
            }
        self._call("sendMessage", chat_id=chat_id, text=GREETING, reply_markup=markup)

    def run(self) -> None:
        """Длинный опрос. Останавливается по Ctrl+C."""
        me = self.whoami()
        logger.info("Бот @%s на связи", me.get("username", "?"))
        self.set_menu_button()

        offset = None
        while True:
            try:
                updates = self._call("getUpdates", offset=offset, timeout=60)
            except (httpx.HTTPError, RuntimeError) as exc:
                logger.warning("Опрос не удался, повтор: %s", exc)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message") or {}
                text = (message.get("text") or "").strip()
                chat = message.get("chat") or {}
                if text.startswith("/start") and chat.get("id"):
                    self._reply(chat["id"])
