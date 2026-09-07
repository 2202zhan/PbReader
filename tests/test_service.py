"""
Тесты локального сервиса. Он даёт интерфейсу PrintBox предпросмотр по URL —
и он же единственная дверь к печати, поэтому проверяем и то, что дверь заперта.
"""

import json
import urllib.error
import urllib.request

import pytest

from pbreader.config import Config
from pbreader.paper import A4
from pbreader.service import PrintBoxService


def _start(tmp_path, with_ui, **config_extra):
    import threading

    config = Config(
        work_dir=tmp_path / "work", host="127.0.0.1", port=0, token="test-token", **config_extra
    )
    server = PrintBoxService(config, with_ui=with_ui)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def service(tmp_path):
    server = _start(tmp_path, with_ui=False)
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def ui_service(tmp_path):
    server = _start(tmp_path, with_ui=True)
    yield server
    server.shutdown()
    server.server_close()


def request(service, path, method="GET", body=None, token="test-token", raw=False):
    url = f"http://127.0.0.1:{service.server_address[1]}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=10) as response:
        payload = response.read()
        return (response, payload) if raw else json.loads(payload)


class TestAccess:
    def test_health_needs_no_token(self, service):
        assert request(service, "/health", token=None)["status"] == "ok"

    def test_everything_else_does(self, service):
        """Сервис умеет печатать и открывать файлы по пути. Без токена любая
        страница, открытая в браузере на этом же компьютере, могла бы этим
        воспользоваться."""
        with pytest.raises(urllib.error.HTTPError) as error:
            request(service, "/printers", token=None)
        assert error.value.code == 401

    def test_wrong_token_is_rejected(self, service):
        with pytest.raises(urllib.error.HTTPError) as error:
            request(service, "/printers", token="wrong-token")
        assert error.value.code == 401


class TestSessions:
    def test_create_returns_the_sheet_plan(self, service, make_pdf):
        path = make_pdf([(A4.size.width, A4.size.height, 0)] * 3)
        data = request(service, "/sessions", "POST", {"file": str(path), "is_one_side": False})
        assert data["document"]["page_count"] == 3
        assert data["sheet_count"] == 2
        assert data["sheets"][0]["back"] == 2

    def test_preview_returns_a_png_with_sheet_facts_in_the_header(self, service, make_pdf):
        path = make_pdf([(A4.size.width, A4.size.height, 0)])
        session = request(service, "/sessions", "POST", {"file": str(path)})
        response, payload = request(
            service, f"/sessions/{session['session_id']}/preview/1?width=200", raw=True
        )
        assert payload[:8] == b"\x89PNG\r\n\x1a\n"
        meta = json.loads(response.headers["X-PbReader-Sheet"])
        assert meta["page"] == 1
        assert meta["scale_mode"] == "actual"

    def test_parameters_can_be_changed_without_resending_the_file(self, service, make_pdf):
        """Человек крутит настройки, а файл уже открыт — пересылать его заново
        на каждое переключение незачем."""
        path = make_pdf([(A4.size.width, A4.size.height, 0)] * 4)
        session = request(service, "/sessions", "POST", {"file": str(path)})
        assert session["sheet_count"] == 4

        updated = request(service, f"/sessions/{session['session_id']}", "PATCH", {"duplex": "long-edge"})
        assert updated["sheet_count"] == 2

        updated = request(service, f"/sessions/{session['session_id']}", "PATCH", {"pages": "1-2"})
        assert updated["pages"] == [1, 2]
        assert updated["job"]["duplex"] == "long-edge"  # прошлая правка не потерялась

    def test_unknown_session_is_a_clear_404(self, service):
        with pytest.raises(urllib.error.HTTPError) as error:
            request(service, f"/sessions/{'0' * 32}")
        assert error.value.code == 404

    def test_bad_page_range_is_reported_not_swallowed(self, service, make_pdf):
        path = make_pdf([(A4.size.width, A4.size.height, 0)])
        with pytest.raises(urllib.error.HTTPError) as error:
            request(service, "/sessions", "POST", {"file": str(path), "pages": "50-60"})
        assert error.value.code == 400
        assert "в нём 1" in json.loads(error.value.read())["error"]

    def test_session_can_be_closed(self, service, make_pdf):
        path = make_pdf([(A4.size.width, A4.size.height, 0)])
        session = request(service, "/sessions", "POST", {"file": str(path)})
        assert request(service, f"/sessions/{session['session_id']}", "DELETE")["closed"] is True
        with pytest.raises(urllib.error.HTTPError):
            request(service, f"/sessions/{session['session_id']}")


class TestUiPage:
    def test_absent_without_the_flag(self, service):
        """`serve` без --ui — это режим для основного проекта: окно там лишнее."""
        with pytest.raises(urllib.error.HTTPError) as error:
            request(service, "/", token=None)
        assert error.value.code == 404
        assert "--ui" in json.loads(error.value.read())["error"]

    def test_served_with_the_flag_and_carries_the_token(self, ui_service):
        response, payload = request(ui_service, "/", token=None, raw=True)
        page = payload.decode("utf-8")
        assert response.headers["Content-Type"].startswith("text/html")
        assert "test-token" in page
        assert "__PBREADER_TOKEN__" not in page

    def test_page_is_the_one_response_without_cors(self, ui_service):
        """Страница несёт токен, поэтому читать её кросс-доменно нельзя.

        Иначе страница из интернета, открытая в браузере на этом же компьютере,
        забрала бы токен и получила право печатать. Остальные методы CORS
        отдают — без токена они всё равно бесполезны.
        """
        page, _ = request(ui_service, "/", token=None, raw=True)
        assert page.headers.get("Access-Control-Allow-Origin") is None

        health, _ = request(ui_service, "/health", token=None, raw=True)
        assert health.headers.get("Access-Control-Allow-Origin") == "*"

    def test_health_reports_whether_the_window_is_available(self, service, ui_service):
        assert request(service, "/health", token=None)["ui"] is False
        assert request(ui_service, "/health", token=None)["ui"] is True


class TestUpload:
    def test_file_sent_from_the_page_becomes_a_session(self, ui_service, make_pdf, tmp_path):
        """В браузере есть содержимое файла, но не его путь — иначе окно не
        смогло бы открыть ничего, кроме уже лежащего на диске у сервиса."""
        source = make_pdf([(A4.size.width, A4.size.height, 0)] * 2)
        url = f"http://127.0.0.1:{ui_service.server_address[1]}/upload"
        req = urllib.request.Request(url, data=source.read_bytes(), method="POST")
        req.add_header("Authorization", "Bearer test-token")
        req.add_header("X-Filename", "%D0%BE%D1%82%D1%87%D1%91%D1%82.pdf")
        with urllib.request.urlopen(req, timeout=10) as response:
            uploaded = json.loads(response.read())

        assert uploaded["name"] == "отчёт.pdf"
        assert uploaded["path"].endswith(".pdf")
        session = request(ui_service, "/sessions", "POST", {"file": uploaded["path"]})
        assert session["document"]["page_count"] == 2

    def test_empty_upload_is_rejected(self, ui_service):
        with pytest.raises(urllib.error.HTTPError) as error:
            request(ui_service, "/upload", "POST", body=None)
        assert error.value.code in (400, 411)


class TestPasswordFlow:
    def test_locked_document_asks_for_a_password_by_code(self, service, locked_pdf):
        """Код нужен окну: «нужен пароль» — это следующий шаг диалога, а не
        поломка, и отличать его от прочих отказов по тексту нельзя."""
        with pytest.raises(urllib.error.HTTPError) as error:
            request(service, "/sessions", "POST", {"file": str(locked_pdf)})
        body = json.loads(error.value.read())
        assert error.value.code == 422
        assert body["code"] == "password_required"

    def test_wrong_password_asks_again(self, service, locked_pdf):
        with pytest.raises(urllib.error.HTTPError) as error:
            request(service, "/sessions", "POST", {"file": str(locked_pdf), "password": "мимо"})
        assert json.loads(error.value.read())["code"] == "password_required"

    def test_correct_password_opens_the_document(self, service, locked_pdf):
        data = request(service, "/sessions", "POST", {"file": str(locked_pdf), "password": "секрет"})
        assert data["document"]["page_count"] == 2

    def test_ordinary_errors_keep_their_own_code(self, service, make_pdf):
        path = make_pdf([(A4.size.width, A4.size.height, 0)])
        with pytest.raises(urllib.error.HTTPError) as error:
            request(service, "/sessions", "POST", {"file": str(path), "pages": "50-60"})
        assert json.loads(error.value.read())["code"] == "error"


class TestPrintJobs:
    def test_unknown_job_is_reported_as_unknown_not_printed(self, service):
        status = request(service, "/print-jobs/424242")
        assert status["state"] == "unknown"
        assert status["confirmed"] is False

    def test_job_list_is_empty_until_something_is_printed(self, service):
        assert request(service, "/print-jobs")["jobs"] == []

    def test_registered_job_appears_with_its_pages(self, service):
        """Регистрация — то, что превращает «отправлено» в отслеживаемое."""
        service.jobs.register(11, "Canon LBP722", total_pages=7, document="акт.pdf")
        listing = request(service, "/print-jobs")["jobs"]
        assert [job["job_id"] for job in listing] == [11]
        assert listing[0]["total_pages"] == 7
        assert listing[0]["document"] == "акт.pdf"

    def test_printer_state_needs_a_printer(self, service):
        with pytest.raises(urllib.error.HTTPError) as error:
            request(service, "/printer-state")
        assert json.loads(error.value.read())["code"] == "no_printer"


class TestHousekeepingWiring:
    def test_service_sweeps_its_work_directory(self, tmp_path):
        """Сервис живёт неделями — уборка должна идти сама, без напоминаний."""
        import os
        import time

        work = tmp_path / "work"
        work.mkdir(parents=True)
        stale = work / "старый.pdf"
        stale.write_bytes(b"x" * 1024)
        moment = time.time() - 100 * 3600
        os.utime(stale, (moment, moment))

        server = _start(tmp_path, with_ui=False)
        try:
            for _ in range(50):
                if not stale.exists():
                    break
                time.sleep(0.05)
            assert not stale.exists()
        finally:
            server.shutdown()
            server.server_close()

    def test_open_documents_are_not_swept(self, tmp_path, make_pdf):
        import os
        import time

        server = _start(tmp_path, with_ui=False)
        try:
            source = make_pdf([(A4.size.width, A4.size.height, 0)])
            moment = time.time() - 100 * 3600
            os.utime(source, (moment, moment))
            session = server.sessions.create(source, __import__("pbreader").PrintJob())
            assert server.housekeeper.sweep_now().kept_in_use >= 0
            assert source.exists()
            server.sessions.drop(session.id)
        finally:
            server.shutdown()
            server.server_close()


@pytest.fixture
def strict_service(tmp_path):
    """Сервис с включённым порогом заполнения — как настроил бы оператор."""
    server = _start(
        tmp_path, with_ui=False,
        max_page_coverage=0.6, max_ink_units=40, enforce_coverage_limit=True,
    )
    yield server
    server.shutdown()
    server.server_close()


class TestCoverageEndpoint:
    def test_ordinary_document_passes(self, service, make_filled_pdf):
        from conftest import FILL_BLANK

        path = make_filled_pdf([FILL_BLANK] * 3)
        session = request(service, "/sessions", "POST", {"file": str(path)})
        data = request(service, f"/sessions/{session['session_id']}/coverage")
        assert data["average_percent"] == 0.0
        assert data["verdict"]["allowed"] is True

    def test_black_pages_are_reported_with_their_cost(self, service, make_filled_pdf):
        from conftest import FILL_BLACK

        path = make_filled_pdf([FILL_BLACK] * 4)
        session = request(service, "/sessions", "POST", {"file": str(path)})
        data = request(service, f"/sessions/{session['session_id']}/coverage")
        assert data["maximum_percent"] > 99
        assert data["total_ink_units"] == pytest.approx(80, rel=0.05)

    def test_coverage_follows_the_parameters(self, service, make_filled_pdf):
        """Уменьшили масштаб — упал и расход: считается лист, а не файл."""
        from conftest import FILL_BLACK

        path = make_filled_pdf([FILL_BLACK])
        session = request(service, "/sessions", "POST", {"file": str(path)})
        full = request(service, f"/sessions/{session['session_id']}/coverage")["total_ink_units"]

        request(service, f"/sessions/{session['session_id']}", "PATCH",
                {"scale": "custom", "scale_percent": 50})
        half = request(service, f"/sessions/{session['session_id']}/coverage")["total_ink_units"]
        assert half == pytest.approx(full / 4, rel=0.1)


class TestCoverageEnforcement:
    def test_printing_a_black_document_is_refused_before_it_starts(self, strict_service, make_filled_pdf):
        """Отказ обязан случиться ДО отправки: после того как задание ушло в
        аппарат, тонер уже потрачен."""
        from conftest import FILL_BLACK

        path = make_filled_pdf([FILL_BLACK] * 4)
        session = request(strict_service, "/sessions", "POST", {"file": str(path)})
        with pytest.raises(urllib.error.HTTPError) as error:
            request(strict_service, f"/sessions/{session['session_id']}/print", "POST", {})
        body = json.loads(error.value.read())
        assert error.value.code == 403
        assert body["code"] == "coverage_limit"
        assert "Заполнение выше" in body["error"]

    def test_the_verdict_says_why(self, strict_service, make_filled_pdf):
        from conftest import FILL_BLACK

        path = make_filled_pdf([FILL_BLACK] * 4)
        session = request(strict_service, "/sessions", "POST", {"file": str(path)})
        verdict = request(strict_service, f"/sessions/{session['session_id']}/coverage")["verdict"]
        assert verdict["allowed"] is False
        assert verdict["enforced"] is True
        assert len(verdict["violations"]) == 2  # и порог страницы, и потолок задания

    def test_an_ordinary_document_is_not_stopped(self, strict_service, make_filled_pdf):
        """Порог не должен мешать обычной печати — иначе его выключат."""
        light = "0 0 0 rg 60 60 200 12 re f"
        path = make_filled_pdf([light] * 4)
        session = request(strict_service, "/sessions", "POST", {"file": str(path)})
        verdict = request(strict_service, f"/sessions/{session['session_id']}/coverage")["verdict"]
        assert verdict["allowed"] is True

    def test_limits_are_reported_so_the_interface_can_explain_them(self, strict_service, make_filled_pdf):
        from conftest import FILL_BLANK

        path = make_filled_pdf([FILL_BLANK])
        session = request(strict_service, "/sessions", "POST", {"file": str(path)})
        limits = request(strict_service, f"/sessions/{session['session_id']}/coverage")["limits"]
        assert limits == {"max_page_coverage": 0.6, "max_ink_units": 40.0, "enforce": True}
