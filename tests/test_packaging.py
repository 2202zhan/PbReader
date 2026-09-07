"""
Сборка в программу. Собрать .exe на Linux нельзя, но всё, из-за чего сборка
обычно ломается молча, проверяется и здесь: потерянные файлы, неверные пути,
точки входа.
"""

import importlib
import sys
from pathlib import Path

import pytest

from PIL import Image

PROJECT = Path(__file__).resolve().parent.parent
PACKAGING = PROJECT / "packaging"


class TestBuildFiles:
    @pytest.mark.parametrize(
        "name",
        ["pbreader.spec", "entry_console.py", "entry_window.py", "make_icon.py",
         "build.ps1", "pbreader.iss", "pbreader.example.json", "README.md"],
    )
    def test_present(self, name):
        assert (PACKAGING / name).is_file()

    def test_spec_points_at_files_that_exist(self):
        """Самая частая поломка сборки — путь к файлу, которого нет: программа
        соберётся, а окно откроется пустым."""
        spec = (PACKAGING / "pbreader.spec").read_text(encoding="utf-8")
        assert '"pbreader" / "ui" / "index.html"' in spec
        assert (PROJECT / "pbreader" / "ui" / "index.html").is_file()

    def test_spec_builds_both_executables(self):
        """Окно без консоли и консольный запуск для основного проекта — разные
        файлы: без консоли нет stdout, а main.js читает именно его."""
        spec = (PACKAGING / "pbreader.spec").read_text(encoding="utf-8")
        assert 'name="PbReader"' in spec and "console=False" in spec
        assert 'name="pbreader"' in spec and "console=True" in spec

    def test_installer_keeps_a_stable_app_id(self):
        """AppId — то, по чему Windows узнаёт установленную программу и ставит
        обновление поверх, а не второй копией."""
        iss = (PACKAGING / "pbreader.iss").read_text(encoding="utf-8")
        assert "AppId={{CF30C9D4-B088-592C-B907-A1B9B95E790A}" in iss

    def test_example_config_is_valid_json(self):
        import json

        data = json.loads((PACKAGING / "pbreader.example.json").read_text(encoding="utf-8"))
        assert data["port"] == 8756

    def test_example_config_keys_exist_in_config(self):
        """Образец не должен обещать настроек, которых программа не знает."""
        import json

        from pbreader.config import Config

        data = json.loads((PACKAGING / "pbreader.example.json").read_text(encoding="utf-8"))
        known = set(Config().__dict__)
        unknown = {key for key in data if not key.startswith("_")} - known
        assert not unknown, f"в образце есть неизвестные ключи: {unknown}"


class TestIcon:
    def test_icon_has_every_size_windows_asks_for(self, tmp_path):
        from packaging_tools import build_icon

        path = build_icon(tmp_path / "test.ico")
        with Image.open(path) as image:
            assert (16, 16) in image.info["sizes"]
            assert (256, 256) in image.info["sizes"]


def _recorder(seen: dict):
    """Подменяет cli.main: запоминает аргументы и возвращает нормальный код."""
    def record(argv):
        seen["argv"] = list(argv)
        return 0

    return record


class TestEntryPoints:
    def test_window_entry_opens_the_window_when_started_with_no_arguments(self, monkeypatch):
        """Двойной щелчок по значку — это «открой окно», а не «покажи справку»."""
        from pbreader import app

        # Модуль должен быть загружен до подмены его функции.
        importlib.import_module("pbreader.cli")

        seen = {}
        monkeypatch.setattr(app, "_log_path", lambda: Path("/tmp/pbreader-test.log"))
        monkeypatch.setattr("pbreader.cli.main", _recorder(seen))
        assert app.main([]) == 0
        assert seen["argv"] == ["ui"]

    def test_window_entry_passes_other_commands_through(self, monkeypatch):
        from pbreader import app

        seen = {}
        monkeypatch.setattr(app, "_log_path", lambda: Path("/tmp/pbreader-test.log"))
        monkeypatch.setattr("pbreader.cli.main", _recorder(seen))
        app.main(["serve", "--port", "9000"])
        assert seen["argv"] == ["serve", "--port", "9000"]

    def test_missing_streams_do_not_crash_a_windowed_process(self, monkeypatch):
        """У окна без консоли нет ни stdout, ни stderr, и запись в них — не
        мелочь: Python на этом падает."""
        from pbreader import app

        monkeypatch.setattr(app, "_log_path", lambda: Path("/tmp/pbreader-test.log"))
        monkeypatch.setattr("pbreader.cli.main", lambda argv: 0)
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)
        assert app.main(["ui"]) == 0
        sys.stdout.write("проверка")  # заглушка, а не None


class TestBundledLayout:
    def test_ui_page_is_found_inside_a_frozen_build(self, tmp_path, monkeypatch):
        """В собранной программе файлы лежат не рядом с модулем, а там, куда их
        распаковал PyInstaller. Промах здесь даёт пустое окно при исправной с
        виду программе."""
        bundle = tmp_path / "_MEI"
        (bundle / "pbreader" / "ui").mkdir(parents=True)
        (bundle / "pbreader" / "ui" / "index.html").write_text("<html>bundled</html>", encoding="utf-8")

        import pbreader.ui as ui

        monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
        assert ui._ui_dir() == bundle / "pbreader" / "ui"

    def test_falls_back_to_the_source_tree(self, monkeypatch):
        import pbreader.ui as ui

        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        assert (ui._ui_dir() / "index.html").is_file()
