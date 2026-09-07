import time

import pytest

from pbreader.job import Duplex, PrintJob
from pbreader.paper import A4
from pbreader.session import SessionExpired, SessionStore


class TestSessionStore:
    def test_job_changes_are_reflected_immediately(self, make_pdf):
        path = make_pdf([(A4.size.width, A4.size.height, 0)] * 4)
        store = SessionStore()
        session = store.create(path, PrintJob())
        assert len(session.sheets) == 4
        session.update_job(session.job.with_(duplex=Duplex.LONG_EDGE))
        assert len(session.sheets) == 2
        store.close_all()

    def test_expired_sessions_are_dropped(self, make_pdf):
        """Киоск работает сутками. Каждая сессия держит открытый PDF, и без
        уборки брошенные задания копятся до перезагрузки."""
        path = make_pdf([(A4.size.width, A4.size.height, 0)])
        store = SessionStore(ttl_seconds=0)
        session = store.create(path, PrintJob())
        time.sleep(0.01)
        with pytest.raises(SessionExpired):
            store.get(session.id)

    def test_oldest_session_is_evicted_when_full(self, make_pdf):
        path = make_pdf([(A4.size.width, A4.size.height, 0)])
        store = SessionStore(max_sessions=2)
        first = store.create(path, PrintJob())
        time.sleep(0.01)
        store.create(path, PrintJob())
        store.create(path, PrintJob())
        with pytest.raises(SessionExpired):
            store.get(first.id)
        store.close_all()

    def test_asking_for_a_sheet_that_does_not_exist_is_a_clear_error(self, make_pdf):
        path = make_pdf([(A4.size.width, A4.size.height, 0)])
        store = SessionStore()
        session = store.create(path, PrintJob())
        with pytest.raises(IndexError, match="в задании 1"):
            session.preview(5)
        store.close_all()
