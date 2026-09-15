"""Токен сессии: выдан нами, подписан нами, живёт ограниченно."""

import time

import pytest

from printhub import sessions

KEY = "ключ-для-теста"


def test_issued_token_names_its_owner():
    assert sessions.verify(sessions.issue(77, KEY, 60), KEY) == 77


def test_a_token_signed_with_another_key_is_refused():
    with pytest.raises(sessions.SessionError):
        sessions.verify(sessions.issue(77, "другой-ключ", 60), KEY)


def test_an_expired_token_is_refused():
    with pytest.raises(sessions.SessionError, match="истёк"):
        sessions.verify(sessions.issue(77, KEY, -1), KEY)


def test_a_token_cannot_be_edited_to_name_someone_else():
    """Подпись покрывает и номер, и срок — переписать нельзя ни то, ни другое."""
    import base64

    body, signature = sessions.issue(77, KEY, 60).split(".", 1)
    payload = base64.urlsafe_b64decode(body + "==").decode()
    forged_payload = payload.replace("77", "78", 1)
    forged = base64.urlsafe_b64encode(forged_payload.encode()).decode().rstrip("=")
    with pytest.raises(sessions.SessionError):
        sessions.verify(f"{forged}.{signature}", KEY)


@pytest.mark.parametrize("junk", ["", "мусор", "а.б", "...", "x." + "y" * 40])
def test_junk_is_refused_without_crashing(junk):
    with pytest.raises(sessions.SessionError):
        sessions.verify(junk, KEY)


def test_a_fresh_token_outlives_a_moment():
    token = sessions.issue(1, KEY, 2)
    time.sleep(0.1)
    assert sessions.verify(token, KEY) == 1
