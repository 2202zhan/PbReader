"""
Печать документов на киоске PrintBox — через PbReader.

Заменяет прежний print.py, который запускал SumatraPDF и Foxit Reader.
Протокол снаружи тот же: JSON на stdin, журнал в stdout и в print.log рядом со
скриптом, результат — кодом возврата. main.js менять не нужно.

В stdout, как и раньше, идёт ТОЛЬКО журнал: печатать туда что-то ещё нельзя —
поток общий, и посторонняя строка попортит то, что main.js оттуда читает.
Итог задания пишется отдельной строкой журнала «RESULT: {...}» — её при желании
можно разобрать, а если не разбирать, ничего не сломается.

ПОЧЕМУ ЗДЕСЬ НЕТ ПУТИ К ПРОГРАММЕ ПЕЧАТИ. SumatraPDF и Foxit — чужие
программы: их надо было найти на диске, развернуть ярлык, поискать в реестре,
запустить процессом и надеяться, что она поймёт флаги. PbReader — обычный
пакет Python: он ставится в окружение и импортируется. Поэтому SUMATRA_PATH и
FOXIT_PATH из config.py больше ни на что не влияют, а нового пути взамен не
появилось.

Ставится из исходников — в PyPI его нет и не планируется:
    pip install .                       (из каталога с репозиторием)
    pip install pbreader-0.1.0-py3-none-any.whl   (из собранного колеса)

ЧТО ИЗМЕНИЛОСЬ ПО СУЩЕСТВУ.

Печать больше не поручается стороннему просмотрщику. PbReader сам растеризует
PDF и сам отдаёт растр драйверу, поэтому лоток, дуплекс, цвет, копии и масштаб
задаём мы, а не то, что согласился принять чужой CLI. Никакое окно поверх
интерфейса киоска не всплывает: всплывать нечему.

Лист в принтер всегда идёт книжным, а альбомная ориентация даётся поворотом
растра. Из-за флага ориентации в DEVMODE Canon UFR II и был особым случаем —
теперь драйверу нечего трактовать по-своему, и деления на «Canon и остальные»
больше нет.

ЛОТОК ТЕПЕРЬ ЧИСЛО. Раньше в tray_bin писали строку вроде "Tray 2": через
SumatraPDF это работало, через Foxit DEVMODE.DefaultSource принимает только
число, и строка молча игнорировалась — лист уезжал в лоток по умолчанию, а
узнать об этом было неоткуда. Теперь строка даёт явную ошибку. Номера лотков
конкретного аппарата показывает:  pbreader printers

ПРОЦЕСС ЖДЁТ КОНЦА ПЕЧАТИ. Код возврата теперь означает «напечаталось», а не
«спулер принял задание»: между этими событиями помещается и замятие, и конец
бумаги. Если в main.js стоит короткий таймаут на процесс, большое задание в
него не уложится — тогда поднимите таймаут либо выставьте PBREADER_NO_WAIT=1.
"""

import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import name_printer, output_dirs as output_dir  # noqa: E402

# Формат бумаги, физически заряженный в лоток. Печатаем всегда на него,
# независимо от формата страниц в документе: иначе принтер запросит бумагу под
# документ, увидит в лотке не то и встанет на паузу до вмешательства человека.
try:
    from config import TARGET_PAPER_SIZE
except ImportError:
    TARGET_PAPER_SIZE = "A4"

import logging  # noqa: E402

# Журнал называется по имени самого скрипта: print.py пишет в print.log, как и
# раньше (его читает main.js по команде get_logs), а пробная копия рядом —
# print_pbreader.py — в свой файл и боевой журнал не засоряет.
LOG_PATH = os.path.splitext(os.path.abspath(__file__))[0] + ".log"
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

# Журнал остаётся print.log рядом со скриптом — его читает main.js по команде
# get_logs, — с полуночной ротацией в logs/print-ГГГГ-ММ-ДД.log.
try:
    from log_rotation import setup_logging

    setup_logging(LOG_PATH, LOG_FORMAT, stream=sys.stdout)
except ImportError:
    # log_rotation.py рядом не оказалось. Это не повод не печатать, но и молчать
    # нельзя: без ротации файл журнала будет расти без предела.
    logging.basicConfig(
        level=logging.INFO, format=LOG_FORMAT,
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger(__name__).warning(
        "log_rotation.py не найден рядом со скриптом — журнал пишется без ротации"
    )

logger = logging.getLogger(__name__)


def _result(payload):
    """Пишет итог задания отдельной строкой журнала.

    Именно строкой журнала, а не в stdout напрямую: stdout здесь — это поток
    журнала, и посторонняя строка в нём испортила бы то, что оттуда читает
    main.js. Прежний print.py по той же причине не печатал в stdout ничего.
    """
    logger.info("RESULT: %s", json.dumps(payload, ensure_ascii=False))


def _fail(message, code=1):
    """Единственный способ закончить неудачей: строка в журнал и код возврата."""
    logger.error(message)
    _result({"ok": False, "error": message})
    sys.exit(code)


try:
    from pbreader import PrintJob
    from pbreader.config import Config
    from pbreader.document import PdfDocument
    from pbreader.formats import UnsupportedFormat
    from pbreader.housekeeping import sweep
    from pbreader.output import JobTracker, print_document
    from pbreader.pipeline import prepare_document
except ImportError as exc:  # pragma: no cover - зависит от окружения аппарата
    _fail(
        f"PbReader не установлен в этом Python ({sys.executable}): {exc}. "
        f"Поставьте пакет из репозитория: pip install . — или из собранного "
        f"колеса: pip install pbreader-0.1.0-py3-none-any.whl"
    )


def _job_from_params(params):
    """Собирает задание PbReader из параметров PrintBox.

    Старые ключи (is_color, is_one_side, is_album_orientation, copy_count,
    custom_pages, is_all_pages, tray_bin) PbReader понимает сам — здесь только
    то, что берётся не из запроса, а из настроек аппарата.
    """
    return PrintJob.from_dict({
        **params,
        "printer": params.get("printer") or name_printer,
        "paper": TARGET_PAPER_SIZE,
    })


def main():
    logger.info("Reading parameters from stdin")
    try:
        params = json.load(sys.stdin)
    except ValueError as exc:
        _fail(f"На stdin не JSON: {exc}", code=2)
    logger.info(f"Parameters received: {params}")

    source = params.get("file_url") or params.get("file") or params.get("path")
    if not source:
        _fail("В параметрах нет ссылки на файл (file_url)", code=2)

    config = Config.load()
    config.work_dir = Path(output_dir)
    config.printer = name_printer
    config.ensure_work_dir()
    logger.info(f"Using output directory: {config.work_dir}")

    # Уборка рабочего каталога: разовый запуск живёт недолго, фонового
    # уборщика у него нет, а мусор от прошлых заданий копится так же.
    try:
        sweep(config.work_dir, config.work_file_ttl_hours)
    except Exception as exc:
        logger.warning(f"Уборка рабочего каталога не удалась: {exc}")

    try:
        job = _job_from_params(params)
    except ValueError as exc:
        # Сюда попадает и нечисловой лоток — сообщение объясняет, что делать.
        _fail(str(exc), code=2)

    logger.info(
        f"Selected printer: {job.printer}, Copies: {job.copies}, "
        f"One-sided: {job.duplex.value == 'simplex'}, Color: {job.color.value}, "
        f"Orientation: {job.orientation.value}, Paper: {job.paper.name}, "
        f"Pages: {job.pages or 'all'}, Tray: {job.tray if job.tray is not None else 'default'}"
    )

    # Прежний код ходил за файлом с verify=False. Если бэкенд отдаёт документы
    # по самоподписанному сертификату, выставьте PBREADER_INSECURE_TLS=1.
    verify_tls = not os.environ.get("PBREADER_INSECURE_TLS")

    try:
        pdf_path = prepare_document(
            str(source), config,
            token=params.get("device_token", ""),
            verify_tls=verify_tls,
            original_name=str(params.get("file_name") or ""),
        )
    except UnsupportedFormat as exc:
        # Человек принёс не тот файл — это про файл, а не про поломку.
        _fail(str(exc), code=2)
    except Exception as exc:
        _fail(f"Не удалось подготовить документ: {exc}")

    logger.info(f"Using PDF: {pdf_path}")

    try:
        with PdfDocument(pdf_path, password=params.get("password") or None) as document:
            result = print_document(
                document, job, document_name=params.get("document_name") or pdf_path.name
            )
    except Exception as exc:
        # Трассировку сплющиваем в одну запись: stdout здесь читает main.js, и
        # многострочная запись ломает построчный разбор. Диагностика при этом
        # никуда не девается — просто едет одной строкой.
        logger.error("Traceback: %s", " | ".join(traceback.format_exc().split("\n")))
        _fail(f"Печать не удалась: {exc}")

    payload = {"ok": True, **result.__dict__}
    logger.info(
        f"Job {result.job_id} submitted: sheets={result.sheets} pages={result.pages_printed} "
        f"dpi={result.dpi}"
    )
    for warning in result.warnings:
        logger.warning(warning)

    # Ждём, пока задание отработает в очереди. «Спулер принял» и «бумага
    # вышла» — разные события, и решать по первому, брать ли деньги, нельзя.
    if result.job_id and not os.environ.get("PBREADER_NO_WAIT"):
        timeout = float(os.environ.get("PBREADER_WAIT_TIMEOUT", "300"))
        tracker = JobTracker()
        tracker.register(result.job_id, result.printer, result.pages_sent, pdf_path.name)
        status = tracker.wait(
            result.job_id,
            timeout=timeout,
            on_change=lambda s: logger.info(
                f"Job {s.job_id}: {s.state.value}"
                + (f" — {s.problem}" if s.problem else "")
                + f" ({s.pages_printed}/{s.total_pages} pages)"
            ),
        )
        payload["status"] = status.to_dict()
        if status.state.value != "printed":
            _fail(status.problem or f"Задание завершилось со статусом «{status.state.value}»")

    _result(payload)
    logger.info("Print process completed successfully")
    sys.exit(0)


if __name__ == "__main__":
    main()
