"""
Слежение за заданием после того, как оно ушло в спулер.

Отправить задание и вернуть «успех» — это отчёт о том, что бумага ПОЕДЕТ, а не
о том, что она вышла. Для платного аппарата разница решающая: человек заплатил,
принтер зажевал лист или остался без тонера, а система считает задание
выполненным. Поэтому у каждого задания есть номер (его возвращает StartDoc), и
по нему у спулера можно спросить, что с ним стало.

Отдельная честность про «задание исчезло». Спулер удаляет запись, как только
она отработала, и после этого GetJob отвечает ошибкой — одинаково и для
напечатанного, и для отменённого, и для никогда не существовавшего. Поэтому
исчезновение задания, которое мы САМИ отправили, считается «напечатано, но не
подтверждено» (`confirmed=False`), а не «напечатано». Врать в эту сторону
нельзя: именно на этом отчёте аппарат решает, брать ли деньги.

Убрать это «не подтверждено» умеет только сам аппарат. У сетевого принтера
есть счётчик механизма — сколько листов он отпечатал за свою жизнь. Прочитанный
до задания и после, он даёт ФИЗИЧЕСКОЕ число вышедших листов; ничего подобного
в Windows нет, а `PagesPrinted` у спулера считает страницы, отданные драйверу.
Оговорка: счётчик общий на аппарат, поэтому чужая печать в тот же момент попадёт
в разницу. Для киоска со своим принтером это не беда, для общего — повод не
доверять числу вслепую.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from threading import RLock
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Флаги состояния задания (winspool.h). В win32con их нет.
JOB_STATUS_PAUSED = 0x00000001
JOB_STATUS_ERROR = 0x00000002
JOB_STATUS_DELETING = 0x00000004
JOB_STATUS_SPOOLING = 0x00000008
JOB_STATUS_PRINTING = 0x00000010
JOB_STATUS_OFFLINE = 0x00000020
JOB_STATUS_PAPEROUT = 0x00000040
JOB_STATUS_PRINTED = 0x00000080
JOB_STATUS_DELETED = 0x00000100
JOB_STATUS_BLOCKED_DEVQ = 0x00000200
JOB_STATUS_USER_INTERVENTION = 0x00000400
JOB_STATUS_RESTART = 0x00000800
JOB_STATUS_COMPLETE = 0x00001000

JOB_CONTROL_CANCEL = 3

#: Неполадки, требующие человека у аппарата. Порядок — по важности: показываем
#: первую подходящую, а не сваливаем всё в кучу.
_PROBLEMS = (
    (JOB_STATUS_PAPEROUT, "нет бумаги"),
    (JOB_STATUS_OFFLINE, "принтер не в сети"),
    (JOB_STATUS_USER_INTERVENTION, "нужно вмешательство: проверьте аппарат"),
    (JOB_STATUS_BLOCKED_DEVQ, "очередь заблокирована"),
    (JOB_STATUS_ERROR, "ошибка печати"),
    (JOB_STATUS_PAUSED, "задание приостановлено"),
)


class SpoolerUnavailable(RuntimeError):
    """К очереди печати не обратиться: не Windows либо нет pywin32."""


def _win32():
    """Модули для работы с очередью. Отдельной функцией — чтобы отсутствие
    Windows превращалось в понятную ошибку, а не в ModuleNotFoundError из
    середины запроса."""
    try:
        import pywintypes
        import win32print
    except ImportError as exc:
        raise SpoolerUnavailable(
            "Очередь печати доступна только в Windows с установленным pywin32"
        ) from exc
    return win32print, pywintypes


class JobState(str, Enum):
    QUEUED = "queued"
    PRINTING = "printing"
    PRINTED = "printed"
    CANCELLED = "cancelled"
    ERROR = "error"
    #: Задания нет в очереди, и мы его не отправляли — сказать о нём нечего.
    UNKNOWN = "unknown"

    @property
    def is_final(self) -> bool:
        return self in (JobState.PRINTED, JobState.CANCELLED, JobState.ERROR)


@dataclass
class CounterBaseline:
    """Показания счётчика механизма на момент отправки задания."""

    host: str
    community: str
    before: int
    expected_sheets: int
    deadline: float
    timeout: float = 2.0


@dataclass
class JobStatus:
    job_id: int
    printer: str
    state: JobState
    #: Видели ли мы финал своими глазами. False — задание просто исчезло из
    #: очереди, и «напечатано» здесь вывод по умолчанию, а не факт.
    confirmed: bool = True
    pages_printed: int = 0
    total_pages: int = 0
    position: int = 0
    problem: str = ""
    document: str = ""
    submitted_at: str = ""
    #: Листов отпечатано механизмом — по счётчику самого аппарата. None, если
    #: спросить некого (принтер не в сети либо SNMP закрыт).
    sheets_marked: int | None = None
    expected_sheets: int = 0

    @property
    def is_final(self) -> bool:
        return self.state.is_final

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "printer": self.printer,
            "state": self.state.value,
            "confirmed": self.confirmed,
            "pages_printed": self.pages_printed,
            "total_pages": self.total_pages,
            "position": self.position,
            "problem": self.problem,
            "document": self.document,
            "submitted_at": self.submitted_at,
            "sheets_marked": self.sheets_marked,
            "expected_sheets": self.expected_sheets,
            "is_final": self.is_final,
        }


def _describe(status: int) -> tuple[JobState, str]:
    """Переводит битовую маску спулера в состояние и понятную человеку причину."""
    problem = next((text for flag, text in _PROBLEMS if status & flag), "")

    if status & (JOB_STATUS_DELETED | JOB_STATUS_DELETING):
        return JobState.CANCELLED, problem
    if status & (JOB_STATUS_PRINTED | JOB_STATUS_COMPLETE):
        return JobState.PRINTED, ""
    # Ошибку проверяем ПОСЛЕ признаков завершения: спулер иногда оставляет
    # взведённым флаг ошибки на задании, которое всё-таки допечаталось.
    if status & (JOB_STATUS_ERROR | JOB_STATUS_PAPEROUT | JOB_STATUS_OFFLINE | JOB_STATUS_USER_INTERVENTION):
        return JobState.ERROR, problem or "ошибка печати"
    if status & JOB_STATUS_PRINTING:
        return JobState.PRINTING, problem
    return JobState.QUEUED, problem


def query_job(printer: str, job_id: int) -> JobStatus | None:
    """Спрашивает у спулера состояние задания. None — задания в очереди нет.

    Поднимает SpoolerUnavailable, если спросить не у кого: это не то же самое,
    что «задания нет», и путать их нельзя — иначе на машине без очереди печати
    любое задание мгновенно объявлялось бы напечатанным.
    """
    win32print, pywintypes = _win32()

    handle = None
    try:
        handle = win32print.OpenPrinter(printer)
        info = win32print.GetJob(handle, job_id, 1)
    except pywintypes.error:
        # Задание отработало и удалено из очереди — либо его там никогда не было.
        # Различить эти случаи может только тот, кто задание отправлял: см. JobTracker.
        return None
    finally:
        if handle is not None:
            try:
                win32print.ClosePrinter(handle)
            except pywintypes.error:
                pass

    state, problem = _describe(int(info.get("Status", 0)))
    submitted = info.get("Submitted")
    return JobStatus(
        job_id=job_id,
        printer=printer,
        state=state,
        confirmed=True,
        pages_printed=int(info.get("PagesPrinted", 0) or 0),
        total_pages=int(info.get("TotalPages", 0) or 0),
        position=int(info.get("Position", 0) or 0),
        problem=problem,
        document=str(info.get("pDocument", "") or ""),
        submitted_at=_format_time(submitted),
    )


def _format_time(value: Any) -> str:
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(int(value)).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return str(value)


def cancel_job(printer: str, job_id: int) -> bool:
    """Снимает задание с печати. True — спулер команду принял."""
    try:
        win32print, pywintypes = _win32()
    except SpoolerUnavailable as exc:
        logger.warning("Отмена задания %d невозможна: %s", job_id, exc)
        return False

    handle = None
    try:
        handle = win32print.OpenPrinter(printer)
        win32print.SetJob(handle, job_id, 0, None, JOB_CONTROL_CANCEL)
        logger.info("Задание %d на %s снято с печати", job_id, printer)
        return True
    except pywintypes.error as exc:
        logger.warning("Не удалось снять задание %d на %s: %s", job_id, printer, exc)
        return False
    finally:
        if handle is not None:
            try:
                win32print.ClosePrinter(handle)
            except pywintypes.error:
                pass


class JobTracker:
    """Помнит отправленные задания — чтобы отличать «отработало» от «не было».

    Сам по себе спулер этой разницы не даёт: и то и другое выглядит как
    отсутствие записи в очереди.
    """

    #: Сколько ждать, пока счётчик механизма догонит очередь. Задание уходит из
    #: очереди раньше, чем последний лист выезжает в приёмник.
    CONFIRM_WINDOW_SECONDS = 120.0

    def __init__(self, keep_finished: int = 64) -> None:
        self.keep_finished = keep_finished
        self._records: dict[int, JobStatus] = {}
        self._baselines: dict[int, CounterBaseline] = {}
        self._order: list[int] = []
        self._lock = RLock()

    def register(
        self,
        job_id: int,
        printer: str,
        total_pages: int,
        document: str = "",
        snmp_host: str | None = None,
        community: str = "public",
        expected_sheets: int = 0,
        snmp_timeout: float = 2.0,
    ) -> JobStatus:
        status = JobStatus(
            job_id=job_id,
            printer=printer,
            state=JobState.QUEUED,
            total_pages=total_pages,
            document=document,
            submitted_at=datetime.now().isoformat(timespec="seconds"),
            expected_sheets=expected_sheets or total_pages,
        )

        baseline = None
        if snmp_host:
            from ..printers.telemetry import page_count

            before = page_count(snmp_host, community=community, timeout=snmp_timeout)
            if before is not None:
                baseline = CounterBaseline(
                    host=snmp_host, community=community, before=before,
                    expected_sheets=status.expected_sheets,
                    deadline=time.monotonic() + self.CONFIRM_WINDOW_SECONDS,
                    timeout=snmp_timeout,
                )
                logger.info(
                    "Задание %d: счётчик механизма до печати — %d листов", job_id, before
                )

        with self._lock:
            self._records[job_id] = status
            if baseline:
                self._baselines[job_id] = baseline
            self._order.append(job_id)
            while len(self._order) > self.keep_finished:
                dropped = self._order.pop(0)
                self._records.pop(dropped, None)
                self._baselines.pop(dropped, None)
        return status

    def _confirm_by_counter(self, status: JobStatus) -> None:
        """Сверяет отпечатанное со счётчиком самого аппарата.

        Счётчик догоняет очередь не сразу: задание считается ушедшим, когда
        спулер отдал последнюю страницу драйверу, а лист в этот момент ещё
        едет. Поэтому пробуем повторно, пока не сойдётся или не выйдет срок.
        """
        with self._lock:
            baseline = self._baselines.get(status.job_id)
        if baseline is None or status.sheets_marked is not None:
            return

        from ..printers.telemetry import page_count

        after = page_count(baseline.host, community=baseline.community, timeout=baseline.timeout)
        if after is None:
            return

        marked = after - baseline.before
        if marked >= baseline.expected_sheets > 0:
            status.sheets_marked = marked
            status.confirmed = True
            logger.info(
                "Задание %d подтверждено аппаратом: отпечатано %d листов (ожидалось %d)",
                status.job_id, marked, baseline.expected_sheets,
            )
        elif time.monotonic() > baseline.deadline:
            status.sheets_marked = max(0, marked)
            status.confirmed = False
            logger.warning(
                "Задание %d: счётчик аппарата вырос на %d при ожидаемых %d — "
                "часть листов не вышла",
                status.job_id, marked, baseline.expected_sheets,
            )

    def status(self, job_id: int) -> JobStatus:
        with self._lock:
            known = self._records.get(job_id)

        if known is None:
            return JobStatus(job_id=job_id, printer="", state=JobState.UNKNOWN, confirmed=False)

        # Задание, уже дошедшее до конца, повторно у спулера не спрашиваем: его
        # записи там давно нет, и ответ был бы «не найдено» на каждый опрос.
        # А вот счётчик механизма ещё может догонять — его дочитываем.
        if known.is_final:
            if known.state is JobState.PRINTED:
                self._confirm_by_counter(known)
            return known

        try:
            live = query_job(known.printer, job_id)
        except SpoolerUnavailable as exc:
            # Не знаем — так и говорим. Объявить задание напечатанным только
            # потому, что спросить не у кого, значит соврать в ту сторону, в
            # которую нельзя: по этому отчёту аппарат берёт деньги.
            known.problem = str(exc)
            known.confirmed = False
            return known

        if live is None:
            known.state = JobState.PRINTED
            known.confirmed = False
            if not known.pages_printed:
                known.pages_printed = known.total_pages
            logger.info(
                "Задание %d исчезло из очереди %s — считаем напечатанным",
                job_id, known.printer,
            )
            # Спулер сказать больше нечего — спрашиваем сам аппарат.
            self._confirm_by_counter(known)
        else:
            live.total_pages = live.total_pages or known.total_pages
            live.document = live.document or known.document
            live.submitted_at = live.submitted_at or known.submitted_at
            known = live
            with self._lock:
                self._records[job_id] = known
            if known.state is JobState.PRINTED:
                self._confirm_by_counter(known)
        return known

    def cancel(self, job_id: int) -> bool:
        with self._lock:
            known = self._records.get(job_id)
        if known is None:
            return False
        if cancel_job(known.printer, job_id):
            known.state = JobState.CANCELLED
            known.confirmed = True
            return True
        return False

    def all(self) -> list[JobStatus]:
        with self._lock:
            ids = list(self._order)
        return [self.status(job_id) for job_id in reversed(ids)]

    def wait(
        self,
        job_id: int,
        timeout: float = 300.0,
        poll: float = 0.5,
        on_change: Callable[[JobStatus], None] | None = None,
    ) -> JobStatus:
        """Ждёт финала задания. Нужен разовой печати из командной строки."""
        deadline = time.monotonic() + timeout
        previous: tuple | None = None
        status = self.status(job_id)
        while not status.is_final and time.monotonic() < deadline:
            signature = (status.state, status.pages_printed, status.problem)
            if on_change and signature != previous:
                on_change(status)
            previous = signature
            time.sleep(poll)
            status = self.status(job_id)
        if on_change:
            on_change(status)
        return status


def printer_state(printer: str) -> dict[str, Any]:
    """Состояние принтера до отправки задания.

    Спросить это ДО того, как аппарат взял деньги, дешевле, чем объясняться
    после.
    """
    win32print, _ = _win32()

    from ..printers.windows import _status_text, list_printers

    info = next((p for p in list_printers() if p.name == printer), None)
    if info is None:
        return {"printer": printer, "ready": False, "status": "принтер не найден", "queued": 0}

    handle = None
    queued = 0
    raw_status = 0
    try:
        handle = win32print.OpenPrinter(printer)
        attributes = win32print.GetPrinter(handle, 2)
        raw_status = int(attributes.get("Status", 0) or 0)
        queued = int(attributes.get("cJobs", 0) or 0)
    except Exception as exc:
        logger.debug("Не удалось прочитать состояние принтера %s: %s", printer, exc)
    finally:
        if handle is not None:
            try:
                win32print.ClosePrinter(handle)
            except Exception:
                pass

    blocking = (
        win32print.PRINTER_STATUS_OFFLINE
        | win32print.PRINTER_STATUS_PAPER_OUT
        | win32print.PRINTER_STATUS_PAPER_JAM
        | win32print.PRINTER_STATUS_NO_TONER
        | win32print.PRINTER_STATUS_DOOR_OPEN
        | win32print.PRINTER_STATUS_ERROR
        | win32print.PRINTER_STATUS_OUT_OF_MEMORY
    )
    return {
        "printer": printer,
        "ready": not (raw_status & blocking),
        "status": _status_text(raw_status),
        "queued": queued,
    }
