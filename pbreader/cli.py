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

`pbreader diagnose --test-page` — когда задание уходит, а бумага чистая: что
драйвер сообщает о выводе растра и доходит ли до листа простая картинка,
нарисованная мимо PDF.
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

    # Уборка перед работой: разовый запуск живёт недолго, фонового уборщика у
    # него нет, а мусор от прошлых заданий копится на диске так же.
    from .housekeeping import sweep

    sweep(config.work_dir, config.work_file_ttl_hours)

    pdf_path = prepare_document(
        str(source), config, token=params.get("device_token", ""), verify_tls=not args.insecure
    )

    from .document import PdfDocument
    from .output import JobTracker, print_document

    # Запись о том, с чем ушло задание. В прежнем print.py такая строка была, и
    # она не украшение: когда с аппарата приходит «напечаталось не то», это
    # единственный след того, что человек на самом деле выбрал.
    logger.info(
        "Задание: файл=%s принтер=%s копий=%d стороны=%s ориентация=%s цвет=%s "
        "лоток=%s страницы=%s масштаб=%s каталог=%s",
        pdf_path.name, job.printer or "по умолчанию", job.copies, job.duplex.value,
        job.orientation.value, job.color.value, job.tray if job.tray is not None else "по умолчанию",
        job.pages or "все", job.scale.value, config.work_dir,
    )

    password = args.password or params.get("password") or None
    with PdfDocument(pdf_path, password=password) as document:
        result = print_document(document, job, document_name=params.get("document_name"))

    payload = {"ok": True, **result.__dict__}

    # По умолчанию ЖДЁМ конца печати. Разница принципиальная: «задание принято
    # спулером» и «бумага вышла» — разные события, и решать по первому, брать
    # ли с человека деньги, нельзя. С --no-wait поведение прежнее.
    if result.job_id and not args.no_wait:
        tracker = JobTracker()
        tracker.register(result.job_id, result.printer, result.pages_sent, document.path.name)
        status = tracker.wait(
            result.job_id,
            timeout=args.wait_timeout,
            on_change=lambda s: logger.info(
                "Задание %d: %s%s (%d/%d стр.)",
                s.job_id, s.state.value, f" — {s.problem}" if s.problem else "",
                s.pages_printed, s.total_pages,
            ),
        )
        payload["status"] = status.to_dict()
        payload["ok"] = status.state.value == "printed"
        if not payload["ok"]:
            payload["error"] = status.problem or f"задание завершилось со статусом «{status.state.value}»"
            print(json.dumps(payload, ensure_ascii=False))
            return 1

    print(json.dumps(payload, ensure_ascii=False))
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

        if not args.no_snmp:
            _print_telemetry(entry["name"], config)
    return 0


def _print_telemetry(printer: str, config: Config) -> None:
    """Показывает то, что знает только сам аппарат: счётчик, бумагу, тонер."""
    from .printers import read_telemetry, resolve_snmp_host

    if not config.snmp_enabled:
        return
    address = resolve_snmp_host(printer, config.snmp_host)
    if not address:
        print("  по сети: подключён локально — опросить нечем")
        return

    info = read_telemetry(printer, community=config.snmp_community,
                          host=config.snmp_host, timeout=config.snmp_timeout)
    if not info.reachable:
        print(f"  по сети ({address}): не отвечает — {info.error}")
        return

    print(f"  по сети ({address}): {info.model or 'аппарат'}"
          + (f", s/n {info.serial}" if info.serial else ""))
    if info.page_count is not None:
        print(f"    отпечатано за всю жизнь: {info.page_count} листов")
    if info.printer_status:
        print(f"    состояние: {info.printer_status}")
    for tray in info.trays:
        level = f"{tray.percent:.0f} %" if tray.percent is not None else (
            "уровень неизвестен" if not tray.level_known else f"{tray.level}"
        )
        print(f"    бумага, {tray.name}: {level}" + ("  ПУСТО" if tray.is_empty else ""))
    for supply in info.supplies:
        level = f"{supply.percent:.0f} %" if supply.percent is not None else "уровень неизвестен"
        print(f"    расходник, {supply.name}: {level}")
    if info.problems:
        print(f"    неполадки: {', '.join(info.problems)}")


def cmd_diagnose(args: argparse.Namespace, config: Config) -> int:
    """Почему на бумаге пусто: что говорит драйвер и доходит ли до неё растр."""
    from .output import diagnose, print_test_page
    from .printers import default_printer

    printer = args.printer or config.printer or default_printer()
    if not printer:
        print("Принтер не задан, и принтера по умолчанию в системе нет")
        return 2

    report = diagnose(printer)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        raster = report["raster"]
        print(f"\nПринтер: {report['printer']}")
        print(f"  разрешение:      {report['dpi']['x']}x{report['dpi']['y']} dpi")
        print(f"  бумага:          {report['paper_px']['width']}x{report['paper_px']['height']} px "
              f"({report['paper_mm']['width']}x{report['paper_mm']['height']} мм)")
        print(f"  область печати:  {report['printable_px']['width']}x{report['printable_px']['height']} px")
        print(f"  смещение:        {report['offset_px']['x']}, {report['offset_px']['y']} px "
              f"(поля {report['margins_mm']['left']} / {report['margins_mm']['top']} мм)")
        print(f"  копий драйвером: {report['driver_copies']}")
        print("  вывод растра:")
        for name, available in raster.items():
            print(f"    {name:20} {'да' if available else 'НЕТ'}")
        if not (raster["StretchDIBits"] or raster["SetDIBitsToDevice"]):
            print("\n  Драйвер не заявляет ни одного способа вывести растр — печать выйдет пустой.")

    _print_telemetry(printer, config)

    if args.test_page:
        print("\nОтправляю пробную страницу (рамка, диагонали, серые полосы)…")
        result = print_test_page(printer)
        print(f"Отправлено, задание {result.job_id}.")
        print("Вышел лист с рамкой — путь до бумаги рабочий, разбираться надо с документом.")
        print("Вышел пустой лист — дело в выводе растра; смотрите предупреждения выше.")
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
    session = store.create(pdf_path, job, password=args.password)
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
    from . import __version__

    parser = argparse.ArgumentParser(prog="pbreader", description="Печать PDF и предпросмотр для PrintBox")
    parser.add_argument("--version", action="version", version=f"PbReader {__version__}")
    parser.add_argument("--config", help="путь к pbreader.json")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    printer = subparsers.add_parser("print", help="напечатать документ")
    printer.add_argument("file", nargs="?", help="путь или URL; можно передать в JSON на stdin")
    printer.add_argument("--stdin", action="store_true", help="взять параметры из JSON на stdin")
    printer.add_argument("--insecure", action="store_true", help="не проверять TLS при скачивании")
    printer.add_argument("--password", help="пароль защищённого PDF")
    printer.add_argument(
        "--no-wait", action="store_true",
        help="не ждать конца печати (вернуться сразу после постановки в очередь)",
    )
    printer.add_argument(
        "--wait-timeout", type=float, default=300.0, dest="wait_timeout",
        help="сколько ждать конца печати, секунд (по умолчанию 300)",
    )
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
    printers.add_argument("--no-snmp", action="store_true", dest="no_snmp",
                          help="не опрашивать аппараты по сети")
    printers.set_defaults(handler=cmd_printers)

    checkup = subparsers.add_parser(
        "diagnose", help="почему на бумаге пусто: возможности драйвера и пробная страница"
    )
    checkup.add_argument("--printer", help="имя принтера (по умолчанию — системный)")
    checkup.add_argument("--test-page", action="store_true", dest="test_page",
                         help="напечатать пробную страницу мимо PDF")
    checkup.add_argument("--json", action="store_true")
    checkup.set_defaults(handler=cmd_diagnose)

    preview = subparsers.add_parser("preview", help="сохранить листы задания в PNG")
    preview.add_argument("file")
    preview.add_argument("--out", default="preview", help="каталог для картинок")
    preview.add_argument("--width", type=int, default=900)
    preview.add_argument("--insecure", action="store_true")
    preview.add_argument("--password", help="пароль защищённого PDF")
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
