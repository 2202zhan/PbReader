import pytest

from pbreader.job import ColorMode, Duplex, Orientation, PrintJob, ScaleMode, parse_page_range


class TestPageRange:
    def test_empty_means_all_pages(self):
        assert parse_page_range("", 4) == [1, 2, 3, 4]
        assert parse_page_range(None, 2) == [1, 2]

    def test_mixed_list_and_ranges(self):
        assert parse_page_range("1,3,5-8", 10) == [1, 3, 5, 6, 7, 8]

    def test_open_ended_ranges(self):
        assert parse_page_range("8-", 10) == [8, 9, 10]
        assert parse_page_range("-3", 10) == [1, 2, 3]

    def test_reversed_range_is_read_as_written(self):
        assert parse_page_range("5-3", 10) == [3, 4, 5]

    def test_repeats_are_preserved(self):
        # «3,1,1» — это страница 3, затем страница 1 дважды: так ведут себя
        # обычные диалоги печати, и так человек печатает второй экземпляр листа.
        assert parse_page_range("3,1,1", 5) == [3, 1, 1]

    def test_range_beyond_document_is_clamped(self):
        assert parse_page_range("8-99", 10) == [8, 9, 10]

    def test_range_entirely_outside_document_is_an_error(self):
        with pytest.raises(ValueError, match="в нём 5"):
            parse_page_range("20-30", 5)

    def test_garbage_is_rejected(self):
        with pytest.raises(ValueError, match="фрагмент"):
            parse_page_range("1,абв", 10)


class TestLegacyProtocol:
    """Старый JSON из main.js должен читаться без изменений на фронтенде."""

    def test_flags_are_translated(self):
        job = PrintJob.from_dict({
            "copy_count": 3,
            "is_color": True,
            "is_one_side": False,
            "is_album_orientation": True,
            "custom_pages": "2-4",
            "tray_bin": "257",
        })
        assert job.copies == 3
        assert job.color is ColorMode.COLOR
        assert job.duplex is Duplex.LONG_EDGE
        assert job.orientation is Orientation.LANDSCAPE
        assert job.pages == "2-4"
        assert job.tray == 257

    def test_is_all_pages_overrides_leftover_custom_pages(self):
        # Фронтенд оставляет прошлый ввод в custom_pages, когда галку «все
        # страницы» вернули обратно. Напечатать при этом надо весь документ.
        job = PrintJob.from_dict({"is_all_pages": True, "custom_pages": "2-4"})
        assert job.pages is None

    def test_non_numeric_tray_is_rejected_loudly(self):
        # Раньше «Tray 2» молча игнорировался и печать уходила в лоток по
        # умолчанию — узнать об этом было неоткуда.
        with pytest.raises(ValueError, match="DMBIN"):
            PrintJob.from_dict({"tray_bin": "Tray 2"})

    def test_native_keys_win_over_legacy(self):
        job = PrintJob.from_dict({"is_color": False, "color": "color"})
        assert job.color is ColorMode.COLOR


class TestValidation:
    def test_zero_copies_rejected(self):
        with pytest.raises(ValueError, match="≥ 1"):
            PrintJob(copies=0)

    def test_custom_scale_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="1–1000"):
            PrintJob(scale=ScaleMode.CUSTOM, scale_percent=0.0)

    def test_round_trip_through_dict(self):
        job = PrintJob(copies=2, duplex=Duplex.SHORT_EDGE, tray=15, pages="1-3")
        assert PrintJob.from_dict(job.to_dict()) == job
