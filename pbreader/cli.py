"""
Точки входа.

`pbreader print` — разовая печать. Совместима со старым протоколом PrintBox:
JSON в stdin с полями is_color / is_one_side / copy_count / custom_pages /
tray_bin, так что main.js можно переключить, не трогая фронтенд.

`pbreader serve` — локальный сервис предпросмотра. По умолчанию без окна: его
поднимает основной проект, и лишний интерфейс там ни к чему. С `--ui` тот же
сервис отдаёт окно программы, с `--no-ui` — принудительно без него.

`pbreader ui` — то же самое плюс открытое окно: PbReader как отдельная
программа, чтобы проверять параметры и предпросмотр без основного проекта.
`pbreader printers` — список принтеров и их лотков (нужен при заведении аппарата:
номера лотков теперь берутся отсюда, а не вбиваются руками).
`pbreader preview` — сохранить листы в PNG, чтобы посмотреть глазами без киоска.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Config
from .job import PrintJob
from .pipeline import prepare_document

logger = logging.getLogger("pbreader")


def _setup_logging(config: Config, verbose: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if config.log_file:
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(config.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def cmd_print(args: argparse.Namespace, config: Config) -> int:
    """Печать одного задания. Параметры — из stdin (JSON) или из аргументов."""
    params: dict = {}
    if args.stdin:
        raw = sys.stdin.read()
        if not raw.strip():
            print(json.dumps({"ok": False, "error": "На stdin пусто, ожидался JSON"}, ensure_ascii=False))
            return 2
        params = json.loads(raw)

    source = args.file or params.get("file") or params.get("file_url") or params.get("path")
    if not source:
        print(json.dumps({"ok": False, "error": "Не указан файл"}, ensure_ascii=False))
        return 2

    overrides = {k: v for k, v in vars(args).items() if k in _JOB_KEYS and v is not None}
    job = PrintJob.from_dict({**params, **overrides})
    if not job.printer:
        job = job.with_(printer=config.printer or _system_default_printer())

    pdf_path = prepare_document(
        str(source), config, token=params.get("device_token", ""), verify_tls=not args.insecure
    )

    from .document import PdfDocument
    from .output import print_document

    with PdfDocument(pdf_path, password=params.get("password")) as document:
        result = print_document(document, job, document_name=params.get("document_name"))

    print(json.dumps({"ok": True, **result.__dict__}, ensure_ascii=False))
    return 0


def cmd_serve(args: argparse.Namespace, config: Config) -> int:
    import threading

    from .service import serve

    if args.port:
        config.port = args.port
    if args.host:
        config.host = args.host

    with_ui = bool(getattr(args, "ui", False))
    service = serve(config, with_ui=with_ui)

    # Токен печатаем в stdout одной строкой JSON: main.js читает его при запуске
    # процесса и дальше подставляет в URL предпросмотра.
    print(
        json.dumps(
            {"url": service.base_url, "token": service.token, "ui": with_ui}, ensure_ascii=False
        ),
        flush=True,
    )

    if with_ui and getattr(args, "open", False):
        from .ui import open_ui

        # Окно получает токен подстановкой на странице, поэтому в адресе его
        # нет — иначе он остался бы в истории браузера.
        open_ui(service.base_url, app_mode=not getattr(args, "browser", False),
                profile_dir=config.work_dir / "ui-profile")

    if with_ui:
        logger.info("Окно программы: %s", service.base_url)
    logger.info("Остановка — Ctrl+C")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print()
    finally:
        service.shutdown()
        service.server_close()
    return 0


def cmd_ui(args: argparse.Namespace, config: Config) -> int:
    """PbReader как отдельная программа: сервис плюс открытое окно."""
    args.ui = True
    args.open = not args.no_open
    return cmd_serve(args, config)


def cmd_printers(args: argparse.Namespace, config: Config) -> int:
    from .printers import default_printer, describe, list_printers

    printers = []
    for info in list_printers():
        entry = info.to_dict()
        try:
            entry["capabilities"] = describe(info.name).to_dict()
        except Exception as exc:
            entry["capabilities_error"] = str(exc)
        printers.append(entry)

    if args.json:
        print(json.dumps({"printers": printers, "default": default_printer()}, ensure_ascii=False, indent=2))
        return 0

    for entry in printers:
        mark = " (по умолчанию)" if entry["is_default"] else ""
        print(f"\n{entry['name']}{mark}\n  драйвер: {entry['driver']}  порт: {entry['port']}  {entry['status']}")
        caps = entry.get("capabilities")
        if not caps:
            print(f"  возможности недоступны: {entry.get('capabilities_error', 'неизвестно')}")
            continue
        print(
            f"  дуплекс: {'да' if caps['supports_duplex'] else 'нет'}   "
            f"цвет: {'да' if caps['supports_color'] else 'нет'}   "
            f"копий драйвером: {caps['max_copies']}   "
            f"разрешение: {caps['max_dpi'] or '?'} dpi"
        )
        if caps["trays"]:
            print("  лотки (идентификатор — имя, значение для поля tray):")
            for tray in caps["trays"]:
                print(f"    {tray['id']:>5}  {tray['name']}")
        else:
            print("  лотки: драйвер список не отдал")
    return 0


def cmd_preview(args: argparse.Namespace, config: Config) -> int:
    """Сохраняет листы задания в PNG — проверить раскладку без аппарата."""
    from .session import SessionStore

    overrides = {k: v for k, v in vars(args).items() if k in _JOB_KEYS and v is not None}
    job = PrintJob.from_dict(overrides)
    if not job.printer:
        job = job.with_(printer=config.printer or _system_default_printer())

    pdf_path = prepare_document(args.file, config, verify_tls=not args.insecure)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    store = SessionStore()
    session = store.create(pdf_path, job)
    try:
        description = session.describe()
        for sheet in description["sheets"]:
            for side in ("front", "back"):
                if side == "back" and sheet["back"] is None and not job.duplex.is_duplex:
                    continue
                preview = session.preview(sheet["number"], side, args.width)
                name = out_dir / f"sheet{sheet['number']:03d}-{side}.png"
                name.write_bytes(preview.png)
                print(f"{name}  стр.{preview.page or '—'}  масштаб {preview.scale_percent}% "
                      f"({preview.scale_mode})" + ("  ОБРЕЗКА" if preview.ink_clipped else ""))
        print(json.dumps(description, ensure_ascii=False, indent=2))
    finally:
        store.close_all()
    return 0


def _system_default_printer() -> str:
    from .printers import IS_WINDOWS

    if not IS_WINDOWS:
        return ""
    from .printers import default_printer

    return default_printer() or ""


_JOB_KEYS = {
    "printer", "paper", "orientation", "color", "duplex", "copies",
    "collate", "pages", "scale", "scale_percent", "tray", "auto_rotate", "print_dpi",
}


def _add_job_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--printer", help="имя принтера (по умолчанию — системный)")
    parser.add_argument("--paper", help="формат бумаги: A4, A3, A5, Letter, Legal")
    parser.add_argument("--orientation", choices=["portrait", "landscape", "auto"])
    parser.add_argument("--color", choices=["color", "monochrome"])
    parser.add_argument("--duplex", choices=["simplex", "long-edge", "short-edge"])
    parser.add_argument("--copies", type=int)
    parser.add_argument("--pages", help="диапазон: 1,3,5-8,12-  (пусто — все)")
    parser.add_argument(
        "--scale", choices=["auto", "actual", "fit", "shrink", "fill", "custom"],
        help="auto: совпал формат — 1:1, иначе вписать",
    )
    parser.add_argument("--scale-percent", type=float, dest="scale_percent")
    parser.add_argument("--tray", type=int, help="идентификатор лотка (см. pbreader printers)")
    parser.add_argument("--print-dpi", type=int, dest="print_dpi")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pbreader", description="Печать PDF и предпросмотр для PrintBox")
    parser.add_argument("--config", help="путь к pbreader.json")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    printer = subparsers.add_parser("print", help="напечатать документ")
    printer.add_argument("file", nargs="?", help="путь или URL; можно передать в JSON на stdin")
    printer.add_argument("--stdin", action="store_true", help="взять параметры из JSON на stdin")
    printer.add_argument("--insecure", action="store_true", help="не проверять TLS при скачивании")
    _add_job_arguments(printer)
    printer.set_defaults(handler=cmd_print)

    server = subparsers.add_parser("serve", help="локальный сервис предпросмотра и печати")
    server.add_argument("--host")
    server.add_argument("--port", type=int)
    server.add_argument(
        "--ui", action=argparse.BooleanOptionalAction, default=False,
        help="отдавать окно программы по адресу сервиса (по умолчанию нет)",
    )
    server.add_argument("--open", action="store_true", help="открыть окно сразу (нужен --ui)")
    server.add_argument("--browser", action="store_true", help="открыть вкладкой браузера, а не окном")
    server.set_defaults(handler=cmd_serve)

    window = subparsers.add_parser("ui", help="открыть PbReader как программу (сервис + окно)")
    window.add_argument("--host")
    window.add_argument("--port", type=int)
    window.add_argument("--no-open", action="store_true", help="не открывать окно, только адрес")
    window.add_argument("--browser", action="store_true", help="открыть вкладкой браузера, а не окном")
    window.set_defaults(handler=cmd_ui)

    printers = subparsers.add_parser("printers", help="принтеры, лотки и возможности")
    printers.add_argument("--json", action="store_true")
    printers.set_defaults(handler=cmd_printers)

    preview = subparsers.add_parser("preview", help="сохранить листы задания в PNG")
    preview.add_argument("file")
    preview.add_argument("--out", default="preview", help="каталог для картинок")
    preview.add_argument("--width", type=int, default=900)
    preview.add_argument("--insecure", action="store_true")
    _add_job_arguments(preview)
    preview.set_defaults(handler=cmd_preview)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config.load(args.config)
    _setup_logging(config, args.verbose)
    try:
        return args.handler(args, config)
    except Exception as exc:
        logger.error("%s", exc, exc_info=args.verbose)
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
