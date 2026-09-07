"""Описание принтера в терминах, не зависящих от Windows (чтобы это было чем тестировать)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Tray:
    """Лоток подачи бумаги.

    `id` — это DMBIN конкретного драйвера. Значения нестандартные и у разных
    моделей разные (у Canon UFR II и HP PCL6 они не совпадут), поэтому лоток
    нельзя «зашить» в конфиг числом из документации — его надо взять у драйвера
    именно этого аппарата.
    """

    id: int
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name}


@dataclass(frozen=True)
class PrinterInfo:
    name: str
    is_default: bool = False
    driver: str = ""
    port: str = ""
    status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "is_default": self.is_default,
            "driver": self.driver,
            "port": self.port,
            "status": self.status,
        }


@dataclass(frozen=True)
class PrinterCapabilities:
    """Что принтер умеет — по словам его же драйвера."""

    name: str
    trays: list[Tray] = field(default_factory=list)
    supports_duplex: bool = False
    supports_color: bool = False
    supports_collate: bool = False
    #: Сколько копий драйвер согласен сделать сам. 1 — копии придётся печатать циклом.
    max_copies: int = 1
    resolutions: list[tuple[int, int]] = field(default_factory=list)
    driver: str = ""
    port: str = ""

    @property
    def max_dpi(self) -> int | None:
        return max((x for x, _ in self.resolutions), default=None)

    def tray_by_id(self, tray_id: int) -> Tray | None:
        return next((t for t in self.trays if t.id == tray_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trays": [t.to_dict() for t in self.trays],
            "supports_duplex": self.supports_duplex,
            "supports_color": self.supports_color,
            "supports_collate": self.supports_collate,
            "max_copies": self.max_copies,
            "resolutions": [list(r) for r in self.resolutions],
            "max_dpi": self.max_dpi,
            "driver": self.driver,
            "port": self.port,
        }
