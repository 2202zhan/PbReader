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
        assert "function applySize" in page

    def test_the_document_itself_can_never_scroll(self):
        """Прокручиваться внутри окна имеют право только настройки и лист.

        Если уедет сам документ, шапка и левая панель уйдут за край, и человек
        останется один на один с картинкой без единой кнопки. overflow:hidden
        от этого не спасает: он запрещает прокрутку мышью, но не программную —
        от scrollIntoView или перевода фокуса. Спасает position:fixed.
        """
        page = INDEX_HTML.read_text(encoding="utf-8")
        assert "html { height: 100%; overflow: hidden; }" in page
        assert "position: fixed; inset: 0;" in page

    def test_zoom_redraws_immediately_and_sharpens_later(self):
        """Щелчок по «+» обязан отзываться сразу: перерисовка листа в высоком
        разрешении занимает сотни миллисекунд, и делать её на каждый щелчок
        значит превратить масштабирование в тормоза."""
        page = INDEX_HTML.read_text(encoding="utf-8")
        assert "scheduleSharpen" in page
        assert "applySize();        // сразу" in page

    def test_zoom_steps_do_not_get_stuck_at_the_limits(self):
        """На 300 % кнопка «мельче» переставала работать: шаг искался как
        «первый больше текущего», а на самом крупном масштабе такого нет.
        Выйти можно было только через «вписать»."""
        page = INDEX_HTML.read_text(encoding="utf-8")
        assert "Math.min(ZOOM_STEPS.length - 1, Math.max(0, index + direction))" in page
        assert "ZOOM_STEPS[index === -1 ? ZOOM_STEPS.length - 1 : index]" not in page

    def test_thumbnails_are_loaded_lazily(self):
        """У документа в сотню листов сотня запросов разом положила бы и
        сервис, и терпение."""
        assert "IntersectionObserver" in INDEX_HTML.read_text(encoding="utf-8")
