"""
API: вход, файлы и границы между людьми.

Главная проверка здесь — не «работает ли загрузка», а «видит ли один
пользователь файлы другого». Всё остальное в сервисе можно починить задним
числом; утечку чужого паспорта починить нельзя.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from printhub import db
from printhub.api.app import create_app
from printhub.config import Settings

from helpers import TOKEN, init_data, login, upload  # общее с другими тестами


@pytest.fixture
def settings(tmp_path):
    return Settings(
        env="dev",
        bot_token=TOKEN,
        secret_key="ключ-для-теста",
        data_dir=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        max_upload_bytes=1024 * 1024,
        dev_login=False,
    )


@pytest.fixture
def client(settings):
    db._Session = None
    with TestClient(create_app(settings)) as test_client:
        yield test_client


class TestLogin:
    def test_a_signed_visitor_gets_a_session_and_a_profile(self, client):
        response = client.post("/api/auth/telegram", json={"init_data": init_data(42)})
        assert response.status_code == 200
        assert response.json()["user"]["id"] == 42
        assert response.json()["token"]

    def test_a_forged_visitor_is_refused(self, client):
        response = client.post(
            "/api/auth/telegram", json={"init_data": init_data(42, token="чужой-токен")}
        )
        assert response.status_code == 401

    def test_the_refusal_does_not_explain_what_exactly_was_wrong(self, client):
        """Разница между «подпись не та» и «срок вышел» помогает подбирающему."""
        bad_signature = client.post(
            "/api/auth/telegram", json={"init_data": init_data(42, token="чужой")}
        ).json()
        no_user = client.post("/api/auth/telegram", json={"init_data": "мусор"}).json()
        assert bad_signature["error"] == no_user["error"]

    def test_without_a_token_nothing_opens(self, client):
        assert client.get("/api/files").status_code == 401
        assert client.get("/api/me").status_code == 401

    @pytest.mark.parametrize("value", [
        "Bearer sam-pridumal",          # просто выдумка
        "Bearer YWJj.ZGVm",             # похоже на настоящий: две части в base64
        "Basic YWJjOmRlZg==",           # не та схема
        "sam-pridumal",                 # вовсе без схемы
    ])
    def test_a_made_up_token_does_not_open_anything(self, client, value):
        assert client.get("/api/files", headers={"Authorization": value}).status_code == 401

    def test_dev_login_is_absent_unless_switched_on(self, client):
        """Вход без телеграма не должен существовать, пока его не включили."""
        assert client.post("/api/auth/dev", json={"user_id": 1}).status_code == 404


class TestFiles:
    def test_an_uploaded_pdf_comes_back_in_the_list(self, client, make_pdf):
        headers = login(client, 42)
        uploaded = upload(client, headers, make_pdf(3, "диплом.pdf"))
        assert uploaded.status_code == 200, uploaded.text
        body = uploaded.json()
        assert body["name"] == "диплом.pdf"
        assert body["format"] == "pdf"
        assert body["pages"] == 3

        listed = client.get("/api/files", headers=headers).json()["files"]
        assert [item["id"] for item in listed] == [body["id"]]

    def test_the_file_can_be_downloaded_back_unchanged(self, client, make_pdf):
        headers = login(client, 42)
        path = make_pdf(1)
        file_id = upload(client, headers, path).json()["id"]
        downloaded = client.get(f"/api/files/{file_id}/content", headers=headers)
        assert downloaded.status_code == 200
        assert downloaded.content == path.read_bytes()

    def test_a_deleted_file_disappears_from_the_list_and_the_disk(self, client, make_pdf, settings):
        headers = login(client, 42)
        file_id = upload(client, headers, make_pdf(1)).json()["id"]
        assert client.delete(f"/api/files/{file_id}", headers=headers).status_code == 200
        assert client.get("/api/files", headers=headers).json()["files"] == []
        assert list(settings.files_dir.glob("*")) == []

    def test_a_book_pretending_to_be_a_pdf_is_named_for_what_it_is(self, client, make_epub):
        """Файл из чужой базы с чужим расширением — обычное дело."""
        headers = login(client, 42)
        response = upload(client, headers, make_epub("книга.pdf"))
        assert response.status_code == 415
        assert "EPUB" in response.json()["error"]

    def test_a_refused_file_leaves_nothing_on_disk(self, client, make_epub, settings):
        headers = login(client, 42)
        upload(client, headers, make_epub())
        assert list(settings.files_dir.glob("*")) == []

    def test_a_file_over_the_limit_is_refused_and_not_kept(self, client, tmp_path, settings):
        """Content-Length можно написать любой — считаем сами, по мере приёма."""
        headers = login(client, 42)
        big = tmp_path / "большой.pdf"
        big.write_bytes(b"%PDF-1.4\n" + b"0" * (settings.max_upload_bytes + 1024))
        assert upload(client, headers, big).status_code == 413
        assert list(settings.files_dir.glob("*")) == []

    def test_an_empty_file_is_refused(self, client, tmp_path):
        headers = login(client, 42)
        empty = tmp_path / "пусто.pdf"
        empty.write_bytes(b"")
        assert upload(client, headers, empty).status_code == 400

    def test_a_name_with_a_path_in_it_does_not_become_a_path(self, client, make_pdf, settings):
        """«../../заметки.pdf» — это имя файла, а не указание, куда его класть."""
        headers = login(client, 42)
        response = upload(client, headers, make_pdf(1), name="../../чужое.pdf")
        assert response.status_code == 200
        assert "/" not in response.json()["name"] and ".." not in response.json()["name"]
        # На диске ровно один файл, и он внутри своего каталога.
        stored = list(settings.files_dir.iterdir())
        assert len(stored) == 1 and stored[0].parent == settings.files_dir


class TestOtherPeoplesFiles:
    """Разные люди — разные файлы. Проверяется каждым способом достать чужое."""

    @pytest.fixture
    def foreign(self, client, make_pdf):
        headers = login(client, 100, "Первый")
        return upload(client, headers, make_pdf(1, "паспорт.pdf")).json()["id"]

    def test_a_stranger_does_not_see_it_in_the_list(self, client, foreign):
        assert client.get("/api/files", headers=login(client, 200, "Второй")).json()["files"] == []

    def test_a_stranger_asking_by_id_is_told_there_is_no_such_file(self, client, foreign):
        """Именно «нет», а не «нельзя»: отказ по правам подтвердил бы, что файл есть."""
        response = client.get(f"/api/files/{foreign}", headers=login(client, 200))
        assert response.status_code == 404

    def test_a_stranger_cannot_download_it(self, client, foreign):
        response = client.get(f"/api/files/{foreign}/content", headers=login(client, 200))
        assert response.status_code == 404

    def test_a_stranger_cannot_delete_it(self, client, foreign):
        assert client.delete(f"/api/files/{foreign}", headers=login(client, 200)).status_code == 404
        owner = login(client, 100, "Первый")
        assert len(client.get("/api/files", headers=owner).json()["files"]) == 1


class TestProfile:
    def test_the_profile_counts_only_ones_own_files(self, client, make_pdf):
        mine = login(client, 42)
        upload(client, mine, make_pdf(1))
        upload(client, mine, make_pdf(1))
        login(client, 43, "Чужой")
        upload(client, login(client, 43, "Чужой"), make_pdf(1))
        assert client.get("/api/me", headers=mine).json()["files_count"] == 2


class TestDevLogin:
    def test_it_works_when_switched_on(self, settings, tmp_path):
        settings.dev_login = True
        db._Session = None
        with TestClient(create_app(settings)) as client:
            response = client.post("/api/auth/dev", json={"user_id": 5})
            assert response.status_code == 200
            headers = {"Authorization": f"Bearer {response.json()['token']}"}
            assert client.get("/api/me", headers=headers).json()["id"] == 5

    def test_it_cannot_exist_on_a_production_server(self, settings):
        """Забыть выключить его при выкатке — вопрос времени. Поэтому не «не
        рекомендуется», а «процесс не поднимется»."""
        settings.env = "prod"
        settings.dev_login = True
        with pytest.raises(RuntimeError, match="PRINTHUB_DEV_LOGIN"):
            settings.validate()


class TestTimestamps:
    def test_times_go_out_with_a_zone(self, client, make_pdf):
        """SQLite зону не хранит, а браузер naive-время читает как местное:
        загруженный минуту назад файл показывался бы загруженным вечером."""
        headers = login(client, 42)
        upload(client, headers, make_pdf(1))
        listed = client.get("/api/files", headers=headers).json()["files"][0]
        assert listed["created_at"].endswith("+00:00")
        parsed = dt.datetime.fromisoformat(listed["created_at"])
        assert parsed.tzinfo is not None
        assert abs((dt.datetime.now(dt.timezone.utc) - parsed).total_seconds()) < 60
