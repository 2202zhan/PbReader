"""Таблицы. Пока две — люди и их файлы; заказы появятся в третьей фазе."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    #: Номер пользователя в телеграме — он же наш. Своей нумерации нет: другого
    #: способа войти сюда не существует, а два ключа на одну сущность — это
    #: рассинхрон, который однажды случится.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    last_name: Mapped[str] = mapped_column(String(128), default="")
    username: Mapped[str] = mapped_column(String(128), default="")
    language_code: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)

    files: Mapped[list["File"]] = relationship(back_populates="owner")


class File(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    #: Как файл называется у человека. Показываем, но на диск под этим именем
    #: не кладём: имя приходит снаружи и содержит что угодно, вплоть до «../».
    original_name: Mapped[str] = mapped_column(Text, default="")
    #: Как он называется на диске — имя, которое придумали мы.
    stored_name: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    format_key: Mapped[str] = mapped_column(String(32), default="")
    format_title: Mapped[str] = mapped_column(String(128), default="")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    #: Удаление мягкое: заказ, который на файл ссылается, должен помнить, что
    #: печатали, даже когда сам файл уже стёрт с диска.
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped[User] = relationship(back_populates="files")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.original_name,
            "size": self.size_bytes,
            "format": self.format_key,
            "format_title": self.format_title,
            "pages": self.page_count,
            "created_at": as_utc(self.created_at),
        }


def as_utc(moment: dt.datetime | None) -> str | None:
    """Время наружу — всегда с зоной.

    SQLite зону не хранит и возвращает naive-время, а браузер такое читает как
    местное. Из Алматы это сдвиг на шесть часов: файл, загруженный минуту назад,
    показывался бы загруженным вечером.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc).isoformat()
