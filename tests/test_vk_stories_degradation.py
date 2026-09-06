"""Тесты деградации VK-сторис с человекочитаемой причиной (фикс бага №1).

Эмпирика (debug-подзадача): сервисный токен приложения валиден (users.get ок),
но stories.get сервисным токеном -> error_code=28 "Application authorization
failed" (нужен user-токен). Значит:
- сервисная ступень — ПОСЛЕДНЯЯ fallback-ступень токенов с явной деградацией
  (db.get_any_active_vk_token_with_tier), а не тихий [];
- get_vk_stories_ex возвращает (stories, reason) с warning-логом на нетокене
  и на VK-ошибке; get_vk_stories — обёртка совместимости;
- checknow_send_all_vk при auth-причине сообщает пользователю причину
  (включая «28») и подсказку: пришлите /token или войдите через /login;
- check_token_works_for_stories при tier="service" и ошибке 28 даёт понятную
  причину с подсказкой /token или /login (интегрируется с token_watcher).

Сеть не используется: vk_call подменяется в namespace модулей.
"""
import asyncio

import db
import vk.client as vk_client
import vk.stories as vk_stories

VK_ERR_28 = "VK error 28: Application authorization failed"


def _run(coro):
    return asyncio.run(coro)


class FakeBot:
    """Fake Telegram bot: собирает тексты send_message."""

    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id=None, text=None, **kwargs):
        self.messages.append(text or "")
        return True


class FakeApp:
    def __init__(self, bot):
        self.bot = bot


# =======================
# 6: get_vk_stories_ex — warning + причина на VK-ошибке
# =======================
def test_get_vk_stories_logs_reason_on_vk_error(caplog, monkeypatch):
    """vk_call -> (False, {}, 'VK error 28: ...') -> warning в логе, возврат ([], reason)."""
    monkeypatch.setattr(db, "get_any_active_vk_token", lambda: "user-token-x")

    async def fake_vk_call(method, params):
        assert method == "stories.get"
        return False, {}, VK_ERR_28

    monkeypatch.setattr(vk_stories, "vk_call", fake_vk_call)

    with caplog.at_level("WARNING", logger="vk.stories"):
        stories, reason = _run(vk_stories.get_vk_stories_ex("123"))

    assert stories == []
    assert reason == VK_ERR_28
    assert any("VK error 28" in rec.getMessage() for rec in caplog.records), (
        "ошибка VK обязана попадать в лог с уровнем warning"
    )


# =======================
# 7: checknow_send_all_vk — сообщение с причиной при auth-ошибке
# =======================
def test_checknow_send_all_vk_reports_auth_reason(monkeypatch):
    """auth-ошибка 28 -> пользователю приходит причина (содержит «28»), не безликий текст."""
    monkeypatch.setattr(db, "get_any_active_vk_token", lambda: "user-token-x")

    async def fake_vk_call(method, params):
        return False, {}, VK_ERR_28

    monkeypatch.setattr(vk_stories, "vk_call", fake_vk_call)

    bot = FakeBot()
    _run(vk_stories.checknow_send_all_vk(FakeApp(bot), 1, "123"))

    assert bot.messages, "пользователь должен получить сообщение"
    assert "28" in bot.messages[0], f"в сообщении должна быть причина: {bot.messages[0]!r}"


# =======================
# 8: check_token_works_for_stories — подсказка на сервисной ступени
# =======================
def test_check_token_works_for_stories_service_token_hint(monkeypatch):
    """tier='service', stories.get -> error 28: причина содержит /token или /login."""
    monkeypatch.setattr(
        db, "get_any_active_vk_token_with_tier", lambda: ("service-token", "service")
    )
    monkeypatch.setattr(db, "get_any_active_vk_token", lambda: "service-token")

    async def fake_vk_call(method, params):
        if method == "users.get":
            return True, {"response": [{"id": 1}]}, ""
        assert method == "stories.get"
        return False, {}, VK_ERR_28

    monkeypatch.setattr(vk_client, "vk_call", fake_vk_call)

    ok, reason = _run(vk_client.check_token_works_for_stories("1"))

    assert ok is False
    assert ("/token" in reason) or ("/login" in reason), (
        f"причина для сервисного токена обязана содержать подсказку: {reason!r}"
    )


# =======================
# 9: checknow_send_all_vk — нет ни одной ступени токена
# =======================
def test_checknow_send_all_vk_no_token_message(monkeypatch):
    """Все ступени пусты -> сообщение пользователю (не тихий возврат)."""
    monkeypatch.setattr(db, "get_any_active_vk_token", lambda: None)
    monkeypatch.setattr(db, "get_any_active_vk_token_with_tier", lambda: (None, ""))

    bot = FakeBot()
    _run(vk_stories.checknow_send_all_vk(FakeApp(bot), 1, "123"))

    assert bot.messages, "пользователь должен получить сообщение, а не тишину"
