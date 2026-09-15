"""
Проверка подписи телеграма.

Это место, где кончается доверие к клиенту: всё, что веб-апп сообщает о себе,
дальше этой функции не проходит. Поэтому проверяется не только «правильное
принимается», но и каждый способ подсунуть неправильное.
"""

import json
import time

import pytest

from printhub.telegram.initdata import InitDataError, sign, validate

TOKEN = "123456:ТЕСТОВЫЙ-ТОКЕН-не-настоящий"
OTHER_TOKEN = "654321:ДРУГОЙ-ТОКЕН"


def make(token=TOKEN, user_id=42, age=0, **extra):
    fields = {
        "user": json.dumps(
            {"id": user_id, "first_name": "Жаке", "username": "zhake"}, ensure_ascii=False
        ),
        "auth_date": str(int(time.time()) - age),
        "query_id": "AAH-test",
        **extra,
    }
    return sign(fields, token)


class TestAccepted:
    def test_a_real_signature_passes(self):
        data = validate(make(), TOKEN)
        assert data.user.id == 42
        assert data.user.first_name == "Жаке"
        assert data.user.display_name == "Жаке"

    def test_start_param_survives(self):
        """Сюда придёт номер аппарата с наклейки на принтере."""
        data = validate(make(start_param="printer_7"), TOKEN)
        assert data.start_param == "printer_7"

    def test_unicode_in_the_name_does_not_break_the_check(self):
        """Имена в телеграме бывают любые, вплоть до эмодзи."""
        fields_user = json.dumps({"id": 7, "first_name": "Айгүл 🌷"}, ensure_ascii=False)
        assert validate(sign(
            {"user": fields_user, "auth_date": str(int(time.time()))}, TOKEN
        ), TOKEN).user.first_name == "Айгүл 🌷"


class TestRejected:
    def test_a_signature_from_another_bot_is_refused(self):
        with pytest.raises(InitDataError):
            validate(make(token=OTHER_TOKEN), TOKEN)

    def test_changing_the_user_after_signing_is_refused(self):
        """Подстановка чужого номера — главный способ влезть в чужой профиль."""
        signed = make(user_id=42)
        forged = signed.replace("42", "999", 1)
        assert forged != signed, "подмена не удалась — тест ничего не проверяет"
        with pytest.raises(InitDataError):
            validate(forged, TOKEN)

    def test_without_a_hash_nothing_is_trusted(self):
        raw = make()
        without = "&".join(p for p in raw.split("&") if not p.startswith("hash="))
        with pytest.raises(InitDataError, match="подпис"):
            validate(without, TOKEN)

    def test_an_old_string_stops_working(self):
        """Перехваченный initData иначе работал бы вечно."""
        with pytest.raises(InitDataError, match="устарел"):
            validate(make(age=7200), TOKEN, max_age_seconds=3600)

    def test_a_timestamp_from_the_future_is_refused(self):
        with pytest.raises(InitDataError, match="будущего"):
            validate(make(age=-3600), TOKEN)

    def test_an_empty_token_refuses_instead_of_signing_anything(self):
        """Пустой токен даёт рабочий ключ — и подпись, которая сходится.

        Если бы проверка на этом не падала, забытая переменная окружения
        превратилась бы во вход для всех желающих.
        """
        with pytest.raises(InitDataError, match="Токен"):
            validate(make(token=""), "")

    def test_empty_input_is_refused(self):
        with pytest.raises(InitDataError):
            validate("", TOKEN)

    def test_a_signed_string_without_a_user_is_refused(self):
        signed = sign({"auth_date": str(int(time.time()))}, TOKEN)
        with pytest.raises(InitDataError, match="пользовател"):
            validate(signed, TOKEN)
