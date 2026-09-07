"""
Замена прежнего print.py — чтобы перейти на PbReader, не трогая main.js.

Кладётся вместо старого print.py. Протокол тот же: JSON на stdin, JSON в
stdout, код возврата 0 или 1. Старые ключи (is_color, is_one_side,
is_album_orientation, copy_count, custom_pages, is_all_pages, tray_bin,
device_token, file_url) читаются как есть — фронтенд и бэкенд менять не нужно.

Прежний config.py, если он лежит рядом, продолжает работать: отсюда берутся
имя принтера и рабочий каталог. Пути к SumatraPDF и Foxit больше не нужны —
PbReader печатает сам.

ДВА ОТЛИЧИЯ ОТ ПРЕЖНЕГО ПОВЕДЕНИЯ, о которых нужно знать заранее:

1. ЛОТОК ТЕПЕРЬ ЧИСЛО. Раньше в tray_bin писали строку вроде "Tray 2": на пути
   через SumatraPDF это работало, на пути через Foxit молча не работало, и лист
   уезжал в лоток по умолчанию. Теперь строка — явная ошибка, а не тишина.
   Числа для конкретного аппарата показывает `pbreader printers`; их и надо
   прописать в панели вместо названий.

2. ПРОЦЕСС ЖДЁТ КОНЦА ПЕЧАТИ и возвращает 1, если не напечаталось (нет бумаги,
   замятие, задание сняли). Раньше код возврата означал «спулер принял
   задание», и на нём нельзя было строить решение об оплате. Если в main.js
   стоит короткий таймаут на процесс, большое задание в него не уложится —
   тогда поднимите таймаут или выставьте PBREADER_NO_WAIT=1, и поведение станет
   прежним.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _adopt_legacy_config() -> None:
    """Переносит настройки из прежнего config.py в окружение PbReader.

    Ничего не требует: если config.py нет или в нём нет нужных имён, берутся
    значения по умолчанию.
    """
    try:
        import config  # type: ignore[import-not-found]
    except Exception:
        return

    printer = getattr(config, "name_printer", None)
    if printer and not os.environ.get("PBREADER_PRINTER"):
        os.environ["PBREADER_PRINTER"] = str(printer)

    # В прежнем config.py каталог назывался output_dirs.
    work_dir = getattr(config, "output_dirs", None) or getattr(config, "output_dir", None)
    if work_dir and not os.environ.get("PBREADER_WORK_DIR"):
        os.environ["PBREADER_WORK_DIR"] = str(work_dir)


def main() -> int:
    _adopt_legacy_config()

    from pbreader.cli import main as pbreader_main

    argv = ["print", "--stdin"]
    if os.environ.get("PBREADER_NO_WAIT"):
        argv.append("--no-wait")
    # Прежний код скачивал файлы с verify=False. Если бэкенд отдаёт документы
    # по самоподписанному сертификату, включите PBREADER_INSECURE_TLS=1 —
    # иначе скачивание теперь честно упадёт на проверке сертификата.
    if os.environ.get("PBREADER_INSECURE_TLS"):
        argv.append("--insecure")
    return pbreader_main(argv)


if __name__ == "__main__":
    sys.exit(main())
