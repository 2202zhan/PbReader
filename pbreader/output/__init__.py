"""Вывод задания на устройство."""

from __future__ import annotations


def print_document(*args, **kwargs):
    """Печатает документ (только Windows). Импорт ленивый: модуль тянет Win32."""
    from .gdi import print_document as _print_document

    return _print_document(*args, **kwargs)


__all__ = ["print_document"]
