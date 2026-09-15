"""Общее для тестов API: вход и загрузка файла."""

import json
import time

from printhub.telegram.initdata import sign

TOKEN = "123456:ТЕСТОВЫЙ-ТОКЕН"


def init_data(user_id: int, first_name: str = "Жаке", token: str = TOKEN) -> str:
    return sign(
        {
            "user": json.dumps({"id": user_id, "first_name": first_name}, ensure_ascii=False),
            "auth_date": str(int(time.time())),
        },
        token,
    )


def login(client, user_id: int, name: str = "Жаке") -> dict:
    response = client.post("/api/auth/telegram", json={"init_data": init_data(user_id, name)})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def upload(client, headers, path, name=None):
    with open(path, "rb") as handle:
        return client.post(
            "/api/files", headers=headers, files={"upload": (name or path.name, handle)}
        )
