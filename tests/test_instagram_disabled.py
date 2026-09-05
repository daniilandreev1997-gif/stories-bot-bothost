"""RED-тест «безопасного отключения» Instagram (Этап 6).

Контракт: instagram.viewer.fetch_stories(username) при ПУСТОМ
config.INSTAGRAM_VIEWER_BASE_URL возвращает [] (без исключения), а не
поднимает NotImplementedError.

Примечание: существующий тест tests/test_app_smoke.py::test_instagram_fetch_stories_not_implemented
зафиксировал текущее поведение (NotImplementedError) и после реализации
этапа 6 будет обновлён code-этапом — конфликт снимается заменой того теста
на этот (поведение меняется намеренно по контракту).
"""
import asyncio

import config


def test_fetch_stories_disabled_without_config(monkeypatch):
    """Пустой INSTAGRAM_VIEWER_BASE_URL -> fetch_stories возвращает [] без исключений."""
    from instagram import fetch_stories

    monkeypatch.setattr(config, "INSTAGRAM_VIEWER_BASE_URL", "")
    result = asyncio.run(fetch_stories("some_user"))
    assert result == []


def test_fetch_stories_disabled_via_viewer_module(monkeypatch):
    """Тот же контракт через прямой импорт instagram.viewer."""
    from instagram import viewer

    monkeypatch.setattr(config, "INSTAGRAM_VIEWER_BASE_URL", "")
    result = asyncio.run(viewer.fetch_stories("@SomeBody"))
    assert result == []
