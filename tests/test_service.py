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


def _start(tmp_path, with_ui):
    import threading

    config = Config(work_dir=tmp_path / "work", host="127.0.0.1", port=0, token="test-token")
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
