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
                                         "sheetImage", "sideTabs", "rail", "zoomIn", "zoomOut",
                                         "zoomFit", "skeleton", "summary"])
    def test_controls_are_in_place(self, element):
        assert f'id="{element}"' in INDEX_HTML.read_text(encoding="utf-8")

    def test_hidden_attribute_is_forced(self):
        """Классы вроде .row и .seg задают display и перебивают атрибут hidden —
        без !important спрятанные блоки остаются видимыми."""
        page = INDEX_HTML.read_text(encoding="utf-8")
        assert "[hidden] { display: none !important; }" in page


class TestLayout:
    """Правила разметки, нарушение которых видно только на живом экране."""

    def test_print_button_lives_outside_the_scrolling_area(self):
        """На аппарате кнопка «Печать» уезжала за нижний край: настройки не
        помещались в окно. Теперь она в отдельной панели снизу, а прокручивается
        только список настроек."""
        page = INDEX_HTML.read_text(encoding="utf-8")
        settings = page.index('class="settings"')
        actions = page.index('class="actions"')
        button = page.index('id="printBtn"')
        assert settings < actions < button, "кнопка должна быть в нижней панели, а не в прокрутке"

    def test_sheet_size_is_set_in_pixels_not_percent(self):
        """У обёртки картинки нет заданной высоты, поэтому max-height:100%
        превращается в none и лист вылезает за нижний край окна."""
        page = INDEX_HTML.read_text(encoding="utf-8")
        assert "image.style.width = `${cssWidth}px`" in page
        assert "function applyFitSize" in page

    def test_thumbnails_are_loaded_lazily(self):
        """У документа в сотню листов сотня запросов разом положила бы и
        сервис, и терпение."""
        assert "IntersectionObserver" in INDEX_HTML.read_text(encoding="utf-8")
