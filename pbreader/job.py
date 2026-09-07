"""
Модель задания на печать.

PrintJob — единственный объект, который описывает «что и как печатать». Его же
целиком принимает предпросмотр. Это и есть гарантия того, что превью и бумага
не разойдутся: у них физически один вход.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from .paper import DEFAULT_PAPER, Paper, get_paper


class Orientation(str, Enum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    #: Ориентацию листа выбирает сама библиотека — по первой странице документа.
    AUTO = "auto"


class ColorMode(str, Enum):
    COLOR = "color"
    MONOCHROME = "monochrome"


class Duplex(str, Enum):
    SIMPLEX = "simplex"
    #: Переворот по длинной стороне — обычная двусторонняя книжка.
    LONG_EDGE = "long-edge"
    #: Переворот по короткой стороне — «блокнотом».
    SHORT_EDGE = "short-edge"

    @property
    def is_duplex(self) -> bool:
        return self is not Duplex.SIMPLEX


class ScaleMode(str, Enum):
    #: 100 %, один пойнт PDF = один пойнт бумаги. То, что вылезло за поля, обрежется.
    ACTUAL = "actual"
    #: Вписать целиком в печатаемую область (может и увеличить).
    FIT = "fit"
    #: Вписать, только если не влезает. Мелкое не растягиваем.
    SHRINK = "shrink"
    #: Заполнить печатаемую область целиком, лишнее обрезать.
    FILL = "fill"
    #: Ручной масштаб, см. PrintJob.scale_percent.
    CUSTOM = "custom"
    #: Формат совпал с бумагой — печатаем 1:1, иначе вписываем.
    AUTO = "auto"


_RANGE_TOKEN = re.compile(r"^\s*(\d+)?\s*(-)?\s*(\d+)?\s*$")


def parse_page_range(spec: str | None, total_pages: int) -> list[int]:
    """Разбирает «1,3,5-8,12-» в список НОМЕРОВ СТРАНИЦ (нумерация с единицы).

    Пустая строка/None означают «все страницы». Открытые диапазоны («5-», «-3»)
    поддерживаются: в киоске человек набирает на экранной клавиатуре и хвост
    диапазона теряется чаще, чем хотелось бы.

    Порядок и кратность задаются пользователем: «3,1,1» — это страница 3, потом
    страница 1 дважды. Именно так ведут себя обычные диалоги печати, и именно
    так человек ожидает напечатать вторую копию одного листа.
    """
    if total_pages <= 0:
        raise ValueError("В документе нет страниц")
    if spec is None or not str(spec).strip():
        return list(range(1, total_pages + 1))

    pages: list[int] = []
    for raw_token in str(spec).replace(";", ",").split(","):
        token = raw_token.strip()
        if not token:
            continue
        match = _RANGE_TOKEN.match(token)
        if not match:
            raise ValueError(f"Не понимаю фрагмент диапазона страниц: {raw_token!r}")
        start_s, dash, end_s = match.groups()
        if dash:
            start = int(start_s) if start_s else 1
            end = int(end_s) if end_s else total_pages
        else:
            if not start_s:
                raise ValueError(f"Не понимаю фрагмент диапазона страниц: {raw_token!r}")
            start = end = int(start_s)
        if start > end:
            start, end = end, start
        clamped = [p for p in range(start, end + 1) if 1 <= p <= total_pages]
        if not clamped:
            raise ValueError(
                f"Страницы {start}-{end} вне документа: в нём {total_pages} стр."
            )
        pages.extend(clamped)

    if not pages:
        raise ValueError(f"Диапазон страниц пуст: {spec!r}")
    return pages


@dataclass(frozen=True)
class PrintJob:
    """Задание целиком. Неизменяемое — превью и печать берут ровно один объект."""

    printer: str = ""
    paper: Paper = DEFAULT_PAPER
    orientation: Orientation = Orientation.PORTRAIT
    color: ColorMode = ColorMode.MONOCHROME
    duplex: Duplex = Duplex.SIMPLEX
    copies: int = 1
    collate: bool = True
    pages: str | None = None
    scale: ScaleMode = ScaleMode.AUTO
    scale_percent: float = 100.0
    #: Лоток: числовой DMBIN конкретного драйвера. None — лоток по умолчанию.
    tray: int | None = None
    #: Довернуть страницу на 90°, если так она ляжет заметно крупнее.
    auto_rotate: bool = False
    #: Разрешение растеризации при печати. None — выбрать по режиму цвета.
    print_dpi: int | None = None

    def __post_init__(self) -> None:
        if self.copies < 1:
            raise ValueError(f"Число копий должно быть ≥ 1, получено: {self.copies}")
        if self.scale is ScaleMode.CUSTOM and not (1.0 <= self.scale_percent <= 1000.0):
            raise ValueError(f"Масштаб вне диапазона 1–1000 %: {self.scale_percent}")

    def with_(self, **changes: Any) -> "PrintJob":
        return replace(self, **changes)

    def resolve_pages(self, total_pages: int) -> list[int]:
        return parse_page_range(self.pages, total_pages)

    @property
    def scale_factor(self) -> float:
        return self.scale_percent / 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "printer": self.printer,
            "paper": self.paper.name,
            "orientation": self.orientation.value,
            "color": self.color.value,
            "duplex": self.duplex.value,
            "copies": self.copies,
            "collate": self.collate,
            "pages": self.pages or "",
            "scale": self.scale.value,
            "scale_percent": self.scale_percent,
            "tray": self.tray,
            "auto_rotate": self.auto_rotate,
            "print_dpi": self.print_dpi,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PrintJob":
        """Собирает задание из JSON. Понимает и «родные» ключи, и старые из print.py.

        Старый протокол PrintBox (is_color / is_one_side / is_album_orientation /
        copy_count / custom_pages / tray_bin) поддерживается специально, чтобы
        main.js можно было переключить на новую печать, не трогая фронтенд.
        """
        def _first(*keys: str, default: Any = None) -> Any:
            for key in keys:
                if key in data and data[key] is not None:
                    return data[key]
            return default

        orientation = _first("orientation")
        if orientation is None:
            album = _first("is_album_orientation", default=False)
            orientation = Orientation.LANDSCAPE if _as_bool(album) else Orientation.PORTRAIT
        else:
            orientation = Orientation(str(orientation).lower())

        color = _first("color")
        if color is None:
            color = ColorMode.COLOR if _as_bool(_first("is_color", default=False)) else ColorMode.MONOCHROME
        else:
            color = ColorMode(str(color).lower())

        duplex = _first("duplex")
        if duplex is None:
            one_side = _as_bool(_first("is_one_side", default=True))
            duplex = Duplex.SIMPLEX if one_side else Duplex.LONG_EDGE
        else:
            duplex = Duplex(str(duplex).lower())

        # is_all_pages из старого протокола главнее custom_pages: фронтенд
        # оставляет в custom_pages прошлый ввод, даже когда галка «все страницы»
        # уже поставлена обратно.
        pages = _first("pages", "custom_pages", default="")
        if _as_bool(_first("is_all_pages", default=False)):
            pages = ""

        scale_raw = _first("scale", default=ScaleMode.AUTO.value)
        scale = ScaleMode(str(scale_raw).lower())

        return cls(
            printer=str(_first("printer", "printer_name", default="") or ""),
            paper=get_paper(_first("paper")),
            orientation=orientation,
            color=color,
            duplex=duplex,
            copies=int(_first("copies", "copy_count", default=1) or 1),
            collate=_as_bool(_first("collate", default=True)),
            pages=str(pages or "") or None,
            scale=scale,
            scale_percent=float(_first("scale_percent", default=100.0) or 100.0),
            tray=_as_tray(_first("tray", "tray_bin")),
            auto_rotate=_as_bool(_first("auto_rotate", default=False)),
            print_dpi=_as_int_or_none(_first("print_dpi", "dpi")),
        )


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "да"}
    return bool(value)


def _as_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _as_tray(value: Any) -> int | None:
    """Лоток в DEVMODE — это ЧИСЛОВОЙ DMBIN драйвера, строка сюда не годится.

    В старой схеме админ вбивал лоток текстом («Tray 2»), и это молча не
    работало на пути через Foxit. Теперь список лотков отдаёт сам драйвер
    (printers.list_trays), поэтому строку принимаем, но только если она —
    число; всё остальное честно роняем, а не глотаем.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"Лоток должен быть числовым идентификатором DMBIN, получено: {value!r}")
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Лоток должен быть числовым идентификатором DMBIN, получено: {value!r}. "
            f"Список лотков принтера отдаёт pbreader.printers.list_trays()."
        ) from exc
