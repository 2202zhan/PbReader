"""
Слежение за заданием в очереди печати.

Проверяется логика учёта, а не Win32: обращение к спулеру подменяется, потому
что интересно именно то, КАК мы толкуем его ответы — в частности исчезнувшее
задание, по которому спулер не отличает напечатанное от отменённого.
"""

import pytest

from pbreader.output import jobs
from pbreader.output.jobs import (
    JOB_STATUS_COMPLETE,
    JOB_STATUS_DELETED,
    JOB_STATUS_ERROR,
    JOB_STATUS_OFFLINE,
    JOB_STATUS_PAPEROUT,
    JOB_STATUS_PAUSED,
    JOB_STATUS_PRINTED,
    JOB_STATUS_PRINTING,
    JOB_STATUS_SPOOLING,
    JobState,
    JobStatus,
    JobTracker,
)


class TestStatusFlags:
    @pytest.mark.parametrize(
        "flags,expected",
        [
            (JOB_STATUS_SPOOLING, JobState.QUEUED),
            (0, JobState.QUEUED),
            (JOB_STATUS_PRINTING, JobState.PRINTING),
            (JOB_STATUS_PRINTED, JobState.PRINTED),
            (JOB_STATUS_COMPLETE, JobState.PRINTED),
            (JOB_STATUS_DELETED, JobState.CANCELLED),
            (JOB_STATUS_PAPEROUT, JobState.ERROR),
            (JOB_STATUS_OFFLINE, JobState.ERROR),
        ],
    )
    def test_state_is_derived_from_the_spooler_mask(self, flags, expected):
        assert jobs._describe(flags)[0] is expected

    def test_problem_is_named_in_words(self):
        assert jobs._describe(JOB_STATUS_PAPEROUT | JOB_STATUS_PRINTING)[1] == "нет бумаги"

    def test_finished_job_wins_over_a_stale_error_flag(self):
        """Спулер иногда оставляет флаг ошибки на задании, которое всё же
        допечаталось. Считать такое ошибкой — значит не взять деньги за
        напечатанное."""
        state, problem = jobs._describe(JOB_STATUS_PRINTED | JOB_STATUS_ERROR)
        assert state is JobState.PRINTED
        assert problem == ""

    def test_paused_job_is_still_waiting_not_broken(self):
        state, problem = jobs._describe(JOB_STATUS_PAUSED)
        assert state is JobState.QUEUED
        assert problem == "задание приостановлено"


@pytest.fixture
def spooler(monkeypatch):
    """Подменяет обращение к очереди печати: тест задаёт ответы сам."""
    answers: dict[int, JobStatus | None] = {}
    calls: list[int] = []

    def fake_query(printer, job_id):
        calls.append(job_id)
        return answers.get(job_id)

    monkeypatch.setattr(jobs, "query_job", fake_query)
    return type("Spooler", (), {"answers": answers, "calls": calls})()


def _live(job_id, state, **kwargs):
    return JobStatus(job_id=job_id, printer="HP", state=state, **kwargs)


class TestTracker:
    def test_registered_job_starts_queued(self):
        tracker = JobTracker()
        status = tracker.register(7, "HP", total_pages=4, document="акт.pdf")
        assert status.state is JobState.QUEUED
        assert status.total_pages == 4

    def test_live_status_comes_from_the_spooler(self, spooler):
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=4)
        spooler.answers[7] = _live(7, JobState.PRINTING, pages_printed=2)
        status = tracker.status(7)
        assert status.state is JobState.PRINTING
        assert status.pages_printed == 2

    def test_vanished_job_is_printed_but_not_confirmed(self, spooler):
        """Спулер удаляет запись сразу после отработки и после этого отвечает
        одинаково на напечатанное, отменённое и никогда не существовавшее.
        Поэтому «исчезло» — это «скорее всего напечатано», и врать в другую
        сторону нельзя: по этому отчёту аппарат решает, брать ли деньги.
        """
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=4)
        spooler.answers[7] = None
        status = tracker.status(7)
        assert status.state is JobState.PRINTED
        assert status.confirmed is False
        assert status.pages_printed == 4

    def test_seen_completion_is_confirmed(self, spooler):
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=4)
        spooler.answers[7] = _live(7, JobState.PRINTED, pages_printed=4)
        assert tracker.status(7).confirmed is True

    def test_finished_job_is_not_asked_about_again(self, spooler):
        """У законченного задания записи в очереди уже нет — повторные опросы
        только шумели бы «не найдено» на каждый тик интерфейса."""
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=1)
        spooler.answers[7] = _live(7, JobState.PRINTED)
        tracker.status(7)
        before = len(spooler.calls)
        tracker.status(7)
        tracker.status(7)
        assert len(spooler.calls) == before

    def test_unknown_job_is_not_guessed_about(self, spooler):
        """Чужой номер задания — это «не знаю», а не «напечатано»."""
        status = JobTracker().status(999)
        assert status.state is JobState.UNKNOWN
        assert status.confirmed is False

    def test_error_keeps_the_reason(self, spooler):
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=4)
        spooler.answers[7] = _live(7, JobState.ERROR, problem="нет бумаги", pages_printed=1)
        status = tracker.status(7)
        assert status.state is JobState.ERROR
        assert status.problem == "нет бумаги"
        assert status.is_final

    def test_old_records_are_dropped(self, spooler):
        tracker = JobTracker(keep_finished=3)
        for job_id in range(1, 6):
            tracker.register(job_id, "HP", total_pages=1)
        assert tracker.status(1).state is JobState.UNKNOWN
        assert tracker.status(5).state is not JobState.UNKNOWN

    def test_cancel_marks_the_job_cancelled(self, spooler, monkeypatch):
        monkeypatch.setattr(jobs, "cancel_job", lambda printer, job_id: True)
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=4)
        assert tracker.cancel(7) is True
        assert tracker.status(7).state is JobState.CANCELLED

    def test_cancel_that_the_spooler_refuses_changes_nothing(self, spooler, monkeypatch):
        monkeypatch.setattr(jobs, "cancel_job", lambda printer, job_id: False)
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=4)
        spooler.answers[7] = _live(7, JobState.PRINTING)
        assert tracker.cancel(7) is False
        assert tracker.status(7).state is JobState.PRINTING

    def test_unreachable_spooler_is_not_read_as_printed(self, monkeypatch):
        """Спросить не у кого — это «не знаю», а не «напечатано».

        Соврать в эту сторону нельзя: именно по такому отчёту аппарат решает,
        брать ли с человека деньги.
        """
        def unavailable(printer, job_id):
            raise jobs.SpoolerUnavailable("нет доступа к очереди печати")

        monkeypatch.setattr(jobs, "query_job", unavailable)
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=4)
        status = tracker.status(7)
        assert status.state is JobState.QUEUED
        assert status.confirmed is False
        assert "очереди печати" in status.problem

    def test_wait_returns_when_the_job_finishes(self, monkeypatch):
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=2)
        seen = []
        sequence = [_live(7, JobState.PRINTING, pages_printed=1), _live(7, JobState.PRINTED, pages_printed=2)]

        def answer(printer, job_id):
            return sequence.pop(0) if sequence else _live(7, JobState.PRINTED, pages_printed=2)

        monkeypatch.setattr(jobs, "query_job", answer)
        status = tracker.wait(7, timeout=5, poll=0.01, on_change=seen.append)
        assert status.state is JobState.PRINTED
        assert any(s.state is JobState.PRINTING for s in seen)


@pytest.fixture
def counter(monkeypatch):
    """Подменяет счётчик механизма: тест задаёт его показания сам."""
    readings = {"value": 1000}

    def fake_page_count(host, community="public", timeout=2.0):
        return readings["value"]

    import pbreader.printers.telemetry as telemetry

    monkeypatch.setattr(telemetry, "page_count", fake_page_count)
    return readings


class TestEngineConfirmation:
    """Счётчик механизма — единственный способ узнать, вышла ли бумага.

    Спулер знает только про очередь; `PagesPrinted` считает страницы, отданные
    драйверу. Разница показаний счётчика до и после — это физические листы.
    """

    def test_counter_turns_a_guess_into_a_fact(self, spooler, counter):
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=2, snmp_host="192.168.1.50", expected_sheets=2)

        counter["value"] = 1002          # аппарат отпечатал два листа
        spooler.answers[7] = None        # задание ушло из очереди
        status = tracker.status(7)

        assert status.state is JobState.PRINTED
        assert status.sheets_marked == 2
        assert status.confirmed is True, "теперь это факт от аппарата, а не вывод"

    def test_a_lagging_counter_is_read_again(self, spooler, counter):
        """Задание уходит из очереди раньше, чем последний лист выезжает."""
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=2, snmp_host="192.168.1.50", expected_sheets=2)
        spooler.answers[7] = None

        counter["value"] = 1001          # вышел только первый лист
        assert tracker.status(7).confirmed is False

        counter["value"] = 1002          # доехал второй
        status = tracker.status(7)
        assert status.sheets_marked == 2
        assert status.confirmed is True

    def test_paper_that_never_came_out_is_reported(self, spooler, counter, monkeypatch):
        """Задание «напечатано», а листов вышло меньше — это и есть тот случай,
        ради которого всё делалось: деньги взяты, бумаги нет."""
        monkeypatch.setattr(JobTracker, "CONFIRM_WINDOW_SECONDS", -1)
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=3, snmp_host="192.168.1.50", expected_sheets=3)
        spooler.answers[7] = None
        counter["value"] = 1001          # вышел один лист из трёх

        status = tracker.status(7)
        assert status.sheets_marked == 1
        assert status.expected_sheets == 3
        assert status.confirmed is False

    def test_without_a_network_printer_nothing_changes(self, spooler):
        """У локального принтера спрашивать некого — поведение прежнее."""
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=2)
        spooler.answers[7] = None
        status = tracker.status(7)
        assert status.sheets_marked is None
        assert status.confirmed is False

    def test_an_unreachable_printer_does_not_break_tracking(self, spooler, monkeypatch):
        import pbreader.printers.telemetry as telemetry

        monkeypatch.setattr(telemetry, "page_count", lambda *a, **k: None)
        tracker = JobTracker()
        tracker.register(7, "HP", total_pages=2, snmp_host="192.168.1.50", expected_sheets=2)
        spooler.answers[7] = None
        status = tracker.status(7)
        assert status.state is JobState.PRINTED
        assert status.sheets_marked is None

    def test_expected_sheets_defaults_to_the_page_count(self, spooler):
        tracker = JobTracker()
        status = tracker.register(7, "HP", total_pages=5)
        assert status.expected_sheets == 5
