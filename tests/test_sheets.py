import pytest

from pbreader.job import Duplex
from pbreader.sheets import emission_order, plan_sheets


class TestSimplex:
    def test_one_page_per_sheet(self):
        sheets = plan_sheets([1, 2, 3], Duplex.SIMPLEX)
        assert [s.front.page for s in sheets] == [1, 2, 3]
        assert all(s.back is None for s in sheets)


class TestDuplex:
    def test_pages_are_paired_onto_sheets(self):
        sheets = plan_sheets([1, 2, 3, 4], Duplex.LONG_EDGE)
        assert len(sheets) == 2
        assert (sheets[0].front.page, sheets[0].back.page) == (1, 2)
        assert (sheets[1].front.page, sheets[1].back.page) == (3, 4)

    def test_odd_page_count_leaves_the_last_back_blank(self):
        sheets = plan_sheets([1, 2, 3], Duplex.LONG_EDGE)
        assert sheets[-1].back.is_blank

    def test_blank_page_separates_copies(self):
        """Без пустой страницы вторая копия начнётся на обороте последнего листа
        первой — человек получит две копии, склеенные через лист."""
        sheets = plan_sheets([1, 2, 3], Duplex.LONG_EDGE, copies=2)
        assert emission_order(sheets) == [1, 2, 3, None, 1, 2, 3]

    def test_trailing_blank_is_not_sent(self):
        # После последнего листа дуплексу уже нечего портить, а лишний чистый
        # лист — это лишний прогон и лишняя бумага.
        assert emission_order(plan_sheets([1, 2, 3], Duplex.LONG_EDGE)) == [1, 2, 3]


class TestCopies:
    def test_collated_copies_repeat_the_whole_document(self):
        sheets = plan_sheets([1, 2], Duplex.SIMPLEX, copies=2, collate=True)
        assert [s.front.page for s in sheets] == [1, 2, 1, 2]

    def test_uncollated_copies_repeat_each_sheet(self):
        sheets = plan_sheets([1, 2], Duplex.SIMPLEX, copies=2, collate=False)
        assert [s.front.page for s in sheets] == [1, 1, 2, 2]

    def test_sheets_are_numbered_through_the_whole_job(self):
        sheets = plan_sheets([1, 2], Duplex.SIMPLEX, copies=2)
        assert [s.number for s in sheets] == [1, 2, 3, 4]
        assert [s.copy for s in sheets] == [1, 1, 2, 2]

    def test_zero_copies_rejected(self):
        with pytest.raises(ValueError, match="≥ 1"):
            plan_sheets([1], Duplex.SIMPLEX, copies=0)
