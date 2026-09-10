"""
Новый print.py для киоска: тот же протокол, другая начинка.

Проверяется то, ради чего замена и существует, — что main.js и config.py менять
не надо, а изменившееся поведение изменилось намеренно и заметно.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT / "integration" / "print.py"

LOG_ROTATION = '''
import logging
from logging.handlers import TimedRotatingFileHandler

def setup_logging(path, fmt, stream=None):
    handlers = [TimedRotatingFileHandler(path, when="midnight", backupCount=14, encoding="utf-8")]
    if stream is not None:
        handlers.append(logging.StreamHandler(stream))
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
'''

LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ - (INFO|WARNING|ERROR|DEBUG) - ")


@pytest.fixture
def kiosk(tmp_path, make_pdf):
    """Раскладка киоска: config.py, log_rotation.py и новый print.py рядом."""
    from pbreader.paper import A4

    (tmp_path / "config.py").write_text(
        f'output_dirs = r"{tmp_path / "output"}"\n'
        f'input_dirs = r"{tmp_path / "input"}"\n'
        'name_printer = "HP LaserJet M507"\n'
        'PRINTER_IP = "192.168.0.2"\n'
        'SUMATRA_PATH = r"C:\\\\SumatraPDF\\\\SumatraPDF.exe"\n'
        'FOXIT_PATH = r"C:\\\\Foxit\\\\FoxitPDFReader.exe"\n'
        'TARGET_PAPER_SIZE = "A4"\n',
        encoding="utf-8",
    )
    (tmp_path / "log_rotation.py").write_text(LOG_ROTATION, encoding="utf-8")
    shutil.copy(SCRIPT, tmp_path / "print.py")
    document = make_pdf([(A4.size.width, A4.size.height, 0)] * 3)
    return tmp_path, document


def run(directory: Path, params, env_extra=None):
    env = {**os.environ, "PYTHONPATH": str(PROJECT)}
    for key in ("PBREADER_NO_WAIT", "PBREADER_INSECURE_TLS", "PBREADER_PRINTER"):
        env.pop(key, None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(directory / "print.py")],
        input=params if isinstance(params, str) else json.dumps(params, ensure_ascii=False),
        capture_output=True, text=True, cwd=directory, env=env, timeout=120,
    )


def result_line(output: str):
    """Достаёт итог задания из журнала."""
    for line in output.splitlines():
        if "RESULT: " in line:
            return json.loads(line.split("RESULT: ", 1)[1])
    return None


class TestProtocol:
    def test_old_json_is_accepted_and_reaches_printing(self, kiosk):
        """Дальше печати на Linux пути нет — но всё до неё должно пройти:
        разбор старых ключей, скачивание, открытие документа, задание."""
        directory, document = kiosk
        done = run(directory, {
            "file_url": str(document), "copy_count": 2, "is_color": False,
            "is_one_side": False, "is_album_orientation": True,
            "is_all_pages": False, "custom_pages": "1-2", "tray_bin": "257",
            "device_token": "тестовый", "telegram_file_id": "x",
            "document_status": "ok", "print_method_status": "pending",
        })
        assert "только в Windows" in done.stdout, "остановились не на печати, а раньше"

    def test_stdout_carries_only_the_log(self, kiosk):
        """В stdout идёт поток журнала, и посторонняя строка испортила бы то,
        что оттуда читает main.js. Прежний print.py по той же причине не
        печатал в stdout ничего."""
        directory, document = kiosk
        done = run(directory, {"file_url": str(document)})
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        assert lines
        assert all(LOG_LINE.match(line) for line in lines)

    def test_the_outcome_is_available_as_one_log_line(self, kiosk):
        directory, document = kiosk
        outcome = result_line(run(directory, {"file_url": str(document)}).stdout)
        assert outcome is not None
        assert outcome["ok"] is False

    def test_log_file_lands_next_to_the_script(self, kiosk):
        """print.log читает main.js по команде get_logs."""
        directory, document = kiosk
        run(directory, {"file_url": str(document)})
        assert (directory / "print.log").is_file()

    def test_it_works_without_log_rotation_but_says_so(self, kiosk):
        """Отсутствие log_rotation.py — не повод не печатать, но и молчать
        нельзя: без ротации журнал будет расти без предела."""
        directory, document = kiosk
        (directory / "log_rotation.py").unlink()
        done = run(directory, {"file_url": str(document)})
        assert "без ротации" in done.stdout
        assert (directory / "print.log").is_file()


class TestSettings:
    def test_printer_and_work_dir_come_from_config(self, kiosk):
        directory, document = kiosk
        done = run(directory, {"file_url": str(document)})
        assert "HP LaserJet M507" in done.stdout
        assert str(directory / "output") in done.stdout

    def test_target_paper_size_is_honoured(self, kiosk):
        """Печатать надо на тот формат, который физически заряжен в лоток:
        иначе принтер запросит бумагу под документ и встанет на паузу."""
        directory, document = kiosk
        config = (directory / "config.py").read_text(encoding="utf-8")
        (directory / "config.py").write_text(
            config.replace('TARGET_PAPER_SIZE = "A4"', 'TARGET_PAPER_SIZE = "LETTER"'), encoding="utf-8"
        )
        done = run(directory, {"file_url": str(document)})
        assert "Paper: Letter" in done.stdout

    def test_a_missing_target_paper_size_defaults_to_a4(self, kiosk):
        directory, document = kiosk
        config = (directory / "config.py").read_text(encoding="utf-8")
        (directory / "config.py").write_text(
            config.replace('TARGET_PAPER_SIZE = "A4"\n', ""), encoding="utf-8"
        )
        done = run(directory, {"file_url": str(document)})
        assert "Paper: A4" in done.stdout


class TestChangedBehaviour:
    def test_tray_name_is_now_a_loud_error(self, kiosk):
        """Раньше «Tray 2» через Foxit молча игнорировался и лист уезжал в
        лоток по умолчанию. Молчание здесь хуже отказа."""
        directory, document = kiosk
        done = run(directory, {"file_url": str(document), "tray_bin": "Tray 2"})
        assert done.returncode == 2
        assert "DMBIN" in result_line(done.stdout)["error"]

    def test_a_book_is_refused_by_its_real_format(self, kiosk):
        """Файл из чужой базы больше не притворяется PDF."""
        import io
        import zipfile

        directory, _ = kiosk
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
        book = directory / "книга.pdf"
        book.write_bytes(buffer.getvalue())

        done = run(directory, {"file_url": str(book)})
        assert done.returncode == 2
        assert "EPUB" in result_line(done.stdout)["error"]


class TestExitCodes:
    def test_missing_file_fails(self, kiosk):
        directory, _ = kiosk
        assert run(directory, {"file_url": str(directory / "нет-такого.pdf")}).returncode == 1

    def test_no_file_in_parameters(self, kiosk):
        directory, _ = kiosk
        assert run(directory, {"copy_count": 1}).returncode == 2

    def test_garbage_on_stdin(self, kiosk):
        directory, _ = kiosk
        done = run(directory, "не json вовсе")
        assert done.returncode == 2
        assert "не JSON" in done.stdout
