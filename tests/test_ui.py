"""Окно программы: подстановка токена и целостность страницы."""

import re

import pytest

from pbreader.ui import INDEX_HTML, TOKEN_PLACEHOLDER, render_ui_page


class TestPage:
    def test_placeholder_is_present_exactly_once(self):
        """Страница получает токен подстановкой. Если разметку отредактируют и
        плейсхолдер потеряется, окно молча перестанет ходить в сервис — а
        выглядеть будет исправным, просто пустым."""
        assert INDEX_HTML.read_text(encoding="utf-8").count(TOKEN_PLACEHOLDER) == 1

    def test_token_is_substituted(self):
        page = render_ui_page("сложный-токен").decode("utf-8")
        assert "сложный-токен" in page
        assert TOKEN_PLACEHOLDER not in page

    def test_page_is_self_contained(self):
        """Ни одной внешней ссылки: аппарат может стоять без интернета, а окно
        обязано открываться одинаково всегда."""
        page = INDEX_HTML.read_text(encoding="utf-8")
        external = re.findall(r'(?:src|href)\s*=\s*"(https?:)?//[^"]+"', page)
        assert external == []

    @pytest.mark.parametrize("element", ["dropZone", "printer", "tray", "duplex", "orientation",
                                         "color", "scale", "pages", "copies", "printBtn",
                                         "sheetImage", "sideTabs"])
    def test_controls_are_in_place(self, element):
        assert f'id="{element}"' in INDEX_HTML.read_text(encoding="utf-8")

    def test_hidden_attribute_is_forced(self):
        """Классы вроде .row и .seg задают display и перебивают атрибут hidden —
        без !important спрятанные блоки остаются видимыми."""
        page = INDEX_HTML.read_text(encoding="utf-8")
        assert "[hidden] { display: none !important; }" in page
