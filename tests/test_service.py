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


@pytest.fixture
def service(tmp_path):
    config = Config(work_dir=tmp_path / "work", host="127.0.0.1", port=0, token="test-token")
    server = PrintBoxService(config)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
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
