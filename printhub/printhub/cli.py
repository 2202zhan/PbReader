"""Запуск: сервер, бот или проверка настроек."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="printhub", description="PrintHub")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="запустить HTTP-сервер")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="перезапуск при правке кода")

    sub.add_parser("bot", help="запустить бота (длинный опрос)")
    sub.add_parser("check", help="показать настройки и проверить их")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        settings = Settings.load()
    except RuntimeError as exc:
        print(f"Настройки не приняты: {exc}", file=sys.stderr)
        return 2

    if args.command == "check":
        print(f"Окружение:        {settings.env}")
        print(f"Каталог данных:   {settings.data_dir.resolve()}")
        print(f"База:             {settings.database_url}")
        print(f"Токен бота:       {'задан' if settings.bot_token else 'НЕ ЗАДАН'}")
        print(f"Ключ подписи:     {'задан' if settings.secret_key else 'НЕ ЗАДАН'}")
        print(f"Адрес веб-аппа:   {settings.webapp_url or 'не задан'}")
        print(f"Вход без телеги:  {'ВКЛЮЧЁН' if settings.dev_login else 'выключен'}")
        if settings.dev_login:
            print("\n  Внимание: при включённом PRINTHUB_DEV_LOGIN войти может кто угодно")
            print("  и под любым номером. Это только для местной разработки.")
        return 0

    if args.command == "bot":
        from .telegram.bot import Bot

        if not settings.bot_token:
            print("PRINTHUB_BOT_TOKEN не задан", file=sys.stderr)
            return 2
        Bot(settings.bot_token, settings.webapp_url).run()
        return 0

    import uvicorn

    uvicorn.run(
        "printhub.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
