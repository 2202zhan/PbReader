"""
Приём файла на диск.

Три вещи, каждая из которых однажды кусается.

Имя файла приходит снаружи. «../../etc/passwd», «C:\\Windows\\...», имя в 4000
символов, имя из одних точек — всё это встречается, и не только от злого умысла.
Поэтому на диск файл ложится под именем, которое придумали мы, а присланное
хранится отдельно как подпись для показа.

Размер приходит снаружи тоже. Заголовку Content-Length верить нельзя: он
необязателен и его никто не сверяет с тем, что реально пришло. Поэтому считаем
байты по мере приёма и обрываем, как только предел превышен, — иначе один
запрос занимает весь диск.

Формат определяется по содержимому, а не по расширению: файл с именем
«диплом.pdf» внутри регулярно оказывается чем угодно.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

#: Читаем кусками, а не целиком: файл на 60 МБ, помноженный на десяток
#: одновременных загрузок, — это уже память сервера.
CHUNK = 1024 * 1024


class UploadTooLarge(ValueError):
    pass


class EmptyUpload(ValueError):
    pass


@dataclass(frozen=True)
class StoredFile:
    stored_name: str
    path: Path
    size_bytes: int
    sha256: str


def display_name(raw: str, fallback: str = "документ") -> str:
    """Имя для показа человеку — без путей и управляющих символов.

    Расширение намеренно не приписывается и не исправляется: формат мы
    определяем по содержимому, и «починенное» расширение только соврало бы.
    """
    name = (raw or "").replace("\\", "/").split("/")[-1].strip()
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')
    name = name.strip(". ")
    if not name:
        return fallback
    return name[:120]


def save(stream: BinaryIO, directory: Path, max_bytes: int) -> StoredFile:
    """Кладёт поток на диск под своим именем, считая размер и контрольную сумму."""
    directory.mkdir(parents=True, exist_ok=True)
    stored_name = uuid.uuid4().hex
    path = directory / stored_name

    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("wb") as target:
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLarge(
                        f"Файл больше разрешённого размера ({max_bytes // (1024 * 1024)} МБ)"
                    )
                digest.update(chunk)
                target.write(chunk)
        if size == 0:
            raise EmptyUpload("Файл пустой")
    except Exception:
        # Недописанный файл на диске не нужен никому: заказа на него уже не
        # будет, а место он занимает так же, как настоящий.
        path.unlink(missing_ok=True)
        raise

    return StoredFile(stored_name=stored_name, path=path, size_bytes=size, sha256=digest.hexdigest())
