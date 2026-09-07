"""
Окно программы: страница, которую отдаёт тот же локальный сервис.

PbReader должен открываться и работать сам по себе — чтобы параметры печати и
предпросмотр можно было проверить, не поднимая основной проект. При этом
заводить ради этого второй движок интерфейса (Qt, tkinter) незачем: сервис уже
отдаёт предпросмотр картинкой по адресу, а браузер уже установлен на любом
аппарате. Поэтому окно — это одна HTML-страница, а «программой» её делает
запуск браузера в режиме приложения (--app), без адресной строки и вкладок.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parent
INDEX_HTML = UI_DIR / "index.html"

#: Подстановка токена в страницу. Строка нарочно не похожа ни на что, что могло
#: бы встретиться в разметке.
TOKEN_PLACEHOLDER = "__PBREADER_TOKEN__"

_APP_MODE_BROWSERS = (
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def render_ui_page(token: str) -> bytes:
    """Возвращает страницу с подставленным токеном."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    return html.replace(TOKEN_PLACEHOLDER, token).encode("utf-8")


def _find_app_browser() -> str | None:
    """Ищет браузер, умеющий режим приложения (окно без адресной строки)."""
    for name in ("chrome", "google-chrome", "chromium", "chromium-browser", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        for path in _APP_MODE_BROWSERS:
            if os.path.exists(path):
                return path
    return None


def open_ui(url: str, app_mode: bool = True, profile_dir: Path | None = None) -> None:
    """Открывает окно программы.

    В режиме приложения окно выглядит как обычная программа: без вкладок и
    адресной строки. Если подходящего браузера нет — открываем как обычную
    страницу; работать будет так же, просто выглядеть иначе.
    """
    browser = _find_app_browser() if app_mode else None
    if browser:
        command = [browser, f"--app={url}", "--window-size=1360,900"]
        if profile_dir:
            # Отдельный профиль: окно программы не подхватывает расширения и
            # сессии рабочего браузера и не мешает уже открытым окнам.
            command.append(f"--user-data-dir={profile_dir}")
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("Окно открыто: %s", browser)
            return
        except OSError as exc:
            logger.warning("Не удалось открыть окно через %s (%s) — открываю в браузере", browser, exc)

    if not webbrowser.open(url):
        logger.warning("Браузер не открылся сам. Откройте вручную: %s", url)


__all__ = ["INDEX_HTML", "TOKEN_PLACEHOLDER", "open_ui", "render_ui_page"]
