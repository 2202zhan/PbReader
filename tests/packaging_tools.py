"""Доступ к скриптам сборки из тестов: каталог packaging не является пакетом."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MAKE_ICON = Path(__file__).resolve().parent.parent / "packaging" / "make_icon.py"


def _load():
    spec = importlib.util.spec_from_file_location("pbreader_make_icon", _MAKE_ICON)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_icon(destination: Path) -> Path:
    return _load().build(Path(destination))
