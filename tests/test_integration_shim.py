"""
Замена прежнего print.py: переключиться, не трогая main.js.

Проверяется то, ради чего замена и существует, — что старый протокол и старый
config.py продолжают работать, а изменившееся поведение изменилось намеренно и
заметно.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
SHIM = PROJECT / "integration" / "print.py"


@pytest.fixture
def kiosk(tmp_path, make_pdf):
    """Воспроизводит аппарат: старый config.py и новый print.py рядом."""
    from pbreader.paper import A4

    (tmp_path / "config.py").write_text(
        'name_printer = "Canon LBP722"\n'
        f'output_dirs = r"{tmp_path / "out"}"\n'
        'SUMATRA_PATH = r"C:\\SumatraPDF\\SumatraPDF.exe"\n',
        encoding="utf-8",
    )
    shutil.copy(SHIM, tmp_path / "print.py")
    document = make_pdf([(A4.size.width, A4.size.height, 0)] * 3)
    return tmp_path, document


def run(directory: Path, params: dict, env_extra: dict | None = None):
    env = {**os.environ, "PYTHONPATH": str(PROJECT)}
    # Настройки самого разработчика не должны просачиваться в тест — но то, что
    # тест задал намеренно, обязано пережить эту чистку.
    env.pop("PBREADER_PRINTER", None)
    env.pop("PBREADER_WORK_DIR", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(directory / "print.py")],
        input=json.dumps(params, ensure_ascii=False),
        capture_output=True, text=True, cwd=directory, env=env, timeout=120,
    )


class TestLegacyProtocol:
    def test_old_json_is_accepted_and_reaches_printing(self, kiosk):
        """Дальше печати на Linux пути нет — но всё до неё должно пройти:
        разбор старых ключей, открытие документа, подготовка задания."""
        directory, document = kiosk
        result = run(directory, {
            "file_url": str(document), "copy_count": 2, "is_color": False,
            "is_one_side": False, "is_album_orientation": True,
            "is_all_pages": False, "custom_pages": "1-2", "tray_bin": "257",
            "device_token": "тестовый",
        })
        body = json.loads(result.stdout.strip().splitlines()[-1])
        assert body["ok"] is False
        assert "только в Windows" in body["error"], "остановились не на печати, а раньше"

    def test_old_config_supplies_the_printer_and_work_dir(self, kiosk):
        directory, document = kiosk
        result = run(directory, {"file_url": str(document)})
        assert "Canon LBP722" in result.stderr
        assert str(directory / "out") in result.stderr

    def test_environment_wins_over_the_old_config(self, kiosk):
        """Настройку всегда можно перебить снаружи, не трогая config.py."""
        directory, document = kiosk
        result = run(directory, {"file_url": str(document)}, {"PBREADER_PRINTER": "HP M507"})
        assert "HP M507" in result.stderr

    def test_missing_config_is_not_an_error(self, tmp_path, make_pdf):
        """config.py может и не быть — тогда работают значения по умолчанию."""
        from pbreader.paper import A4

        shutil.copy(SHIM, tmp_path / "print.py")
        document = make_pdf([(A4.size.width, A4.size.height, 0)])
        result = run(tmp_path, {"file_url": str(document)})
        assert "только в Windows" in result.stdout


class TestChangedBehaviour:
    def test_tray_name_is_now_a_loud_error(self, kiosk):
        """Раньше «Tray 2» на пути через Foxit молча игнорировался и лист уезжал
        в лоток по умолчанию. Молчание здесь хуже отказа: узнать о подмене было
        неоткуда."""
        directory, document = kiosk
        result = run(directory, {"file_url": str(document), "tray_bin": "Tray 2"})
        body = json.loads(result.stdout.strip().splitlines()[-1])
        assert result.returncode == 1
        assert "DMBIN" in body["error"]

    def test_failure_is_reported_by_the_exit_code(self, kiosk):
        """main.js читает код возврата — по нему и решает, что делать дальше."""
        directory, _ = kiosk
        result = run(directory, {"file_url": str(directory / "нет-такого.pdf")})
        assert result.returncode == 1

    def test_stdout_stays_one_json_line(self, kiosk):
        """Журнал уходит в stderr, ответ — в stdout: main.js разбирает его как
        раньше, одной строкой."""
        directory, document = kiosk
        result = run(directory, {"file_url": str(document)})
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["ok"] is False
