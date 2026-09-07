"""Уборка рабочего каталога: аппарат работает месяцами, файлы копятся всегда."""

import os
import time

from pbreader.housekeeping import Housekeeper, sweep


def _aged(path, hours):
    moment = time.time() - hours * 3600
    os.utime(path, (moment, moment))
    return path


def _file(directory, name, size=2048, age_hours=0.0):
    path = directory / name
    path.write_bytes(b"x" * size)
    return _aged(path, age_hours) if age_hours else path


class TestSweep:
    def test_old_files_are_removed(self, tmp_path):
        _file(tmp_path, "вчерашний.pdf", age_hours=40)
        assert sweep(tmp_path, max_age_hours=24).removed == 1
        assert not (tmp_path / "вчерашний.pdf").exists()

    def test_fresh_files_are_left_alone(self, tmp_path):
        _file(tmp_path, "свежий.pdf")
        assert sweep(tmp_path, max_age_hours=24).removed == 0
        assert (tmp_path / "свежий.pdf").exists()

    def test_files_of_open_sessions_survive_regardless_of_age(self, tmp_path):
        """По этим файлам прямо сейчас показывают предпросмотр."""
        busy = _file(tmp_path, "открытый.pdf", age_hours=100)
        result = sweep(tmp_path, max_age_hours=24, keep=[busy])
        assert result.removed == 0
        assert result.kept_in_use == 1
        assert busy.exists()

    def test_freed_space_is_reported(self, tmp_path):
        _file(tmp_path, "большой.pdf", size=3 * 1024 * 1024, age_hours=40)
        assert sweep(tmp_path, max_age_hours=24).freed_mb == 3.0

    def test_subdirectories_are_not_touched(self, tmp_path):
        """В рабочем каталоге лежит и профиль окна программы — не наш мусор."""
        nested = tmp_path / "ui-profile"
        nested.mkdir()
        _aged(nested, 200)
        sweep(tmp_path, max_age_hours=24)
        assert nested.is_dir()

    def test_missing_directory_is_not_an_error(self, tmp_path):
        assert sweep(tmp_path / "нет-такого", max_age_hours=1).removed == 0


class TestHousekeeper:
    def test_sweep_now_uses_the_live_list_of_open_files(self, tmp_path):
        busy = _file(tmp_path, "открытый.pdf", age_hours=100)
        stale = _file(tmp_path, "брошенный.pdf", age_hours=100)
        keeper = Housekeeper(tmp_path, max_age_hours=24, keep_provider=lambda: [busy])
        result = keeper.sweep_now()
        assert result.removed == 1
        assert busy.exists() and not stale.exists()

    def test_a_broken_keep_list_does_not_stop_the_service(self, tmp_path):
        """Уборка не та задача, ради которой стоит ронять печать."""
        def explode():
            raise RuntimeError("список сессий недоступен")

        keeper = Housekeeper(tmp_path, keep_provider=explode)
        assert keeper.sweep_now().removed == 0

    def test_interval_never_goes_below_a_minute(self, tmp_path):
        assert Housekeeper(tmp_path, interval_minutes=0).interval_seconds == 60.0
