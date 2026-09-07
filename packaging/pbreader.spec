# -*- mode: python ; coding: utf-8 -*-
"""
Сборка PbReader в программу (PyInstaller).

Собираются ДВА исполняемых файла в один каталог:

* ``PbReader.exe``  — оконный, без консоли. Его запускает человек с рабочего
  стола, и чёрное окно консоли поверх интерфейса киоска здесь недопустимо.
* ``pbreader.exe``  — консольный, полная командная строка. Его запускает
  основной проект и читает stdout, поэтому консоль ему нужна.

Оба лежат рядом и делят одни и те же библиотеки: PDFium весит около восьми
мегабайт, класть его в сборку дважды незачем.

Сборка: pyinstaller packaging/pbreader.spec --noconfirm
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

SPEC_DIR = Path(SPECPATH).resolve()
PROJECT_DIR = SPEC_DIR.parent
ICON = SPEC_DIR / "pbreader.ico"

# Страница окна — обычный файл, а не модуль: PyInstaller сам её не заметит.
DATAS = [(str(PROJECT_DIR / "pbreader" / "ui" / "index.html"), "pbreader/ui")]

# libpdfium лежит внутри pypdfium2_raw и подгружается через ctypes — такие
# библиотеки анализатор импортов тоже не находит, их нужно назвать явно.
BINARIES = collect_dynamic_libs("pypdfium2_raw")

HIDDEN = ["pypdfium2_raw"]

if sys.platform == "win32":
    HIDDEN += [
        # Win32: подтягиваются лениво, внутри функций печати, — анализатор
        # импортов их не видит.
        "win32print", "win32gui", "win32con", "pywintypes", "win32com.client",
        # COM-конвертация офисных форматов
        "comtypes", "comtypes.client",
    ]

EXCLUDES = [
    # Тянутся транзитивно и весят десятки мегабайт, а программе не нужны.
    "tkinter", "numpy", "pytest", "playwright", "setuptools", "pip",
    "matplotlib", "IPython", "pandas",
]


def analyse(script: str) -> Analysis:
    return Analysis(
        [str(SPEC_DIR / script)],
        pathex=[str(PROJECT_DIR)],
        binaries=BINARIES,
        datas=DATAS,
        hiddenimports=HIDDEN,
        excludes=EXCLUDES,
        noarchive=False,
    )


window = analyse("entry_window.py")
console = analyse("entry_console.py")

MERGE((window, "entry_window", "PbReader"), (console, "entry_console", "pbreader"))

window_pyz = PYZ(window.pure, window.zipped_data)
console_pyz = PYZ(console.pure, console.zipped_data)

window_exe = EXE(
    window_pyz, window.scripts, [],
    exclude_binaries=True,
    name="PbReader",
    icon=str(ICON) if ICON.exists() else None,
    console=False,          # без консоли: окно программы не должно мигать чёрным
    disable_windowed_traceback=False,
)

console_exe = EXE(
    console_pyz, console.scripts, [],
    exclude_binaries=True,
    name="pbreader",
    icon=str(ICON) if ICON.exists() else None,
    console=True,           # основной проект читает stdout — консоль обязательна
)

COLLECT(
    window_exe, window.binaries, window.datas,
    console_exe, console.binaries, console.datas,
    strip=False,
    upx=False,              # UPX ломает подпись и настораживает антивирусы
    name="PbReader",
)
