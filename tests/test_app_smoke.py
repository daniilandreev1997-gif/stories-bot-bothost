"""Smoke-тесты сборки приложения (подзадача A3): entrypoint, IG-контракт, handlers."""
import asyncio

import pytest

import bot_host


def test_bot_host_entrypoint_points_to_app_main():
    # bot_host.py — тонкий entrypoint: main реэкспортирован из app
    assert bot_host.main.__module__ == "app"


def test_instagram_fetch_stories_disabled_returns_empty_list():
    """Этап 6: при пустом INSTAGRAM_VIEWER_BASE_URL fetch_stories -> [] (без исключения).

    Поведение изменено намеренно (было: NotImplementedError); полный контракт
    «безопасного отключения» — в tests/test_instagram_disabled.py.
    """
    from instagram import fetch_stories

    result = asyncio.run(fetch_stories("x"))
    assert result == []


def test_normalize_username_working():
    from instagram import normalize_username

    assert normalize_username("@SomeBody") == "somebody"
    assert normalize_username("https://www.instagram.com/nick/?hl=ru") == "nick"
    assert normalize_username("bad name!") == ""


def test_build_application_has_handlers():
    import app

    application = app.build_application()
    assert hasattr(application, "handlers")


def test_register_handlers_without_exceptions():
    import app

    application = app.build_application()
    app.register_handlers(application)  # не должен поднять исключений
    total_handlers = sum(len(handlers) for handlers in application.handlers.values())
    assert total_handlers >= 10  # 9 CommandHandler + MessageHandler
