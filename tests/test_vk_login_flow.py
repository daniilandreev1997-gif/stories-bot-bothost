"""Тесты VK-входа по логину/паролю (tg/vk_login_flows.py, docs/design-vk-login.md §8).

Инфраструктура: db_isolated (tmp-БД), autouse no_network, мок vk_direct_auth на
уровне tg.vk_login_flows, FakeUpdate/FakeContext вместо PTB-объектов.
Секреты (пароль/токен/код) никогда не попадают в логи и тексты (TestNoSecrets).
"""
import asyncio

import pytest

import config
import db
import tg.vk_login_flows as vk_flows
from tg import handle_text, start_cmd
from tg.keyboards import BUTTON_VK_LOGIN, WAIT_STATE_KEYS
from tg.vk_login_flows import (
    _clear_vk_login_ctx,
    _parse_vk_login_input,
    ask_vk_login,
    set_vk_captcha_from_text,
    set_vk_code_from_text,
    set_vk_login_from_text,
)

TG_ID = 424242
LOGIN = "user_login@mail.tld"
PASSWORD = "sup3r-SECRET-pa:ss"
TOKEN = "vk1.a.ACCESS_TOKEN_value"
CAPTCHA_URL = "https://api.vk.com/captcha.jpg?sid=42"


# --- Фейки Telegram-объектов ---
class FakeMessage:
    def __init__(self, text="", chat_id=TG_ID):
        self.text = text
        self.chat_id = chat_id
        self.texts = []
        self.photos = []

    async def reply_text(self, text, **kwargs):
        self.texts.append(text)

    async def reply_photo(self, photo=None, **kwargs):
        self.photos.append(photo)


class FakeUpdate:
    def __init__(self, message):
        self.message = message


class FakeContext:
    def __init__(self):
        self.user_data = {}


def make_update(text=""):
    return FakeUpdate(FakeMessage(text=text))


def last_reply(update):
    assert update.message.texts, "бот не ответил текстом"
    return update.message.texts[-1]


def mock_auth(monkeypatch, result, calls):
    """Подменяет vk_direct_auth в namespace tg.vk_login_flows; пишет вызовы."""

    async def fake_vk_direct_auth(login, password, **kwargs):
        calls.append({"login": login, "password": password, **kwargs})
        return dict(result)

    monkeypatch.setattr(vk_flows, "vk_direct_auth", fake_vk_direct_auth)


def run(coro):
    return asyncio.run(coro)


OK_RESULT = {"ok": True, "access_token": TOKEN, "user_id": "456789"}
NEED_VALIDATION_RESULT = {
    "ok": False,
    "error": "need_validation",
    "validation_sid": "sid-777",
    "phone_mask": "+7***123",
}
CAPTCHA_RESULT = {
    "ok": False,
    "error": "need_captcha",
    "captcha_sid": "captcha-sid-42",
    "captcha_img": CAPTCHA_URL,
}


# --- §8.1 Парсер ---
class TestParseVkLoginInput:
    def test_ok_email_password(self):
        assert _parse_vk_login_input("user@mail.tld:secret") == ("user@mail.tld", "secret")

    def test_ok_phone_password_without_at(self):
        # Отличие от TikTok-парсера: '@' не требуется, регистр сохраняется.
        assert _parse_vk_login_input("+79001234567:PassWord") == ("+79001234567", "PassWord")

    def test_strips_whitespace(self):
        assert _parse_vk_login_input("  user@mail.tld : secret  ") == ("user@mail.tld", "secret")

    def test_colon_in_password_splits_on_first(self):
        assert _parse_vk_login_input("user:pa:ss:wd") == ("user", "pa:ss:wd")

    def test_password_with_dollar(self):
        assert _parse_vk_login_input("user:p$ss$word") == ("user", "p$ss$word")

    def test_unicode(self):
        assert _parse_vk_login_input("логин:пароль123") == ("логин", "пароль123")

    def test_no_colon_invalid(self):
        assert _parse_vk_login_input("user_without_colon") is None
        assert _parse_vk_login_input("") is None
        assert _parse_vk_login_input(None) is None

    def test_empty_login_invalid(self):
        assert _parse_vk_login_input(":pass") is None
        assert _parse_vk_login_input("   :pass") is None

    def test_empty_password_invalid(self):
        assert _parse_vk_login_input("user:") is None
        assert _parse_vk_login_input("user:   ") is None


# --- §8.2 Гейт ---
class TestGate:
    def test_gate_disabled_without_credentials(self, monkeypatch):
        monkeypatch.setattr(config, "VK_LOGIN_ENABLED", False)
        update, context = make_update(), FakeContext()
        run(ask_vk_login(update, context))
        assert "временно недоступен" in last_reply(update)
        assert not any(context.user_data.get(k) for k in WAIT_STATE_KEYS)

    def test_gate_enabled_with_credentials(self, monkeypatch):
        monkeypatch.setattr(config, "VK_LOGIN_ENABLED", True)
        update, context = make_update(), FakeContext()
        run(ask_vk_login(update, context))
        assert "login:password" in last_reply(update)
        assert context.user_data.get("await_vk_login")

    def test_gate_force_disabled_by_env_flag(self, monkeypatch):
        monkeypatch.setattr(config, "VK_LOGIN_ENABLED", False)
        update, context = make_update(BUTTON_VK_LOGIN), FakeContext()
        run(handle_text(update, context))
        assert "временно недоступен" in last_reply(update)
        assert not context.user_data.get("await_vk_login")


# --- §8.3 Маппинг статусов ---
class TestOkFlow:
    def test_ok_saves_token_per_user(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, OK_RESULT, calls)
        monkeypatch.setattr(vk_flows, "vk_call", _mock_vk_call_ok())

        update, context = make_update(), FakeContext()
        run(ask_vk_login(update, context))
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))

        assert db.get_vk_user_token(TG_ID) == TOKEN
        # Запрос ушёл в vk_direct_auth с подготовленными кредами.
        assert calls[0]["login"] == LOGIN
        assert calls[0]["password"] == PASSWORD
        assert calls[0]["client_id"] == config.VK_DIRECT_AUTH_CLIENT_ID
        assert calls[0]["client_secret"] == config.VK_DIRECT_AUTH_CLIENT_SECRET

    def test_ok_clears_ctx_and_wait_state(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, OK_RESULT, calls)
        monkeypatch.setattr(vk_flows, "vk_call", _mock_vk_call_ok())
        update, context = make_update(), FakeContext()
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))
        assert not any(context.user_data.get(k) for k in WAIT_STATE_KEYS)
        assert context.user_data.get("vk_login_ctx") is None
        assert "сохранён" in last_reply(update)

    def test_ok_store_password_flag_calls_save_vk_user_password(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, OK_RESULT, calls)
        monkeypatch.setattr(vk_flows, "vk_call", _mock_vk_call_ok())
        monkeypatch.setattr(config, "VK_STORE_PASSWORD", True)

        password_calls = []

        def spy_save_password(tg_id, login, password):
            password_calls.append((tg_id, login, password))

        monkeypatch.setattr(db, "save_vk_user_password", spy_save_password)

        update, context = make_update(), FakeContext()
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))
        assert password_calls == [(TG_ID, LOGIN, PASSWORD)]
        # REPLACE-семантика: итоговая строка — токен.
        assert db.get_vk_user_token(TG_ID) == TOKEN

    def test_ok_no_store_password_by_default(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, OK_RESULT, calls)
        monkeypatch.setattr(vk_flows, "vk_call", _mock_vk_call_ok())

        password_calls = []

        def spy_save_password(tg_id, login, password):
            password_calls.append((tg_id, login, password))

        monkeypatch.setattr(db, "save_vk_user_password", spy_save_password)

        update, context = make_update(), FakeContext()
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))
        assert password_calls == []

    def test_post_check_failure_still_saves_token(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, OK_RESULT, calls)
        monkeypatch.setattr(vk_flows, "vk_call", _mock_vk_call_fail())

        update, context = make_update(), FakeContext()
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))
        # Токен сохранён несмотря на фейл post-check; текст — warning.
        assert db.get_vk_user_token(TG_ID) == TOKEN
        assert "токен сохранён" in last_reply(update)

    def test_ok_response_text_without_secrets(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, OK_RESULT, calls)
        monkeypatch.setattr(vk_flows, "vk_call", _mock_vk_call_ok())
        update, context = make_update(), FakeContext()
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))
        reply = last_reply(update)
        assert TOKEN not in reply
        assert PASSWORD not in reply
        assert LOGIN not in reply


class TestNeedValidation:
    def test_need_validation_sets_code_state_with_phone_mask(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, NEED_VALIDATION_RESULT, calls)
        update, context = make_update(), FakeContext()
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))

        assert context.user_data.get("await_vk_code")
        reply = last_reply(update)
        assert "+7***123" in reply
        ctx = context.user_data.get("vk_login_ctx")
        assert ctx["validation_sid"] == "sid-777"
        assert ctx["phone_mask"] == "+7***123"
        assert ctx["login"] == LOGIN

    def test_code_context_persists_between_messages(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, NEED_VALIDATION_RESULT, calls)
        update, context = make_update(), FakeContext()
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))

        # Второе сообщение: код -> ok.
        mock_auth(monkeypatch, OK_RESULT, calls)
        monkeypatch.setattr(vk_flows, "vk_call", _mock_vk_call_ok())
        run(set_vk_code_from_text(update, context, "123456"))

        assert db.get_vk_user_token(TG_ID) == TOKEN
        assert calls[1]["code"] == "123456"
        assert calls[1]["login"] == LOGIN
        assert context.user_data.get("vk_login_ctx") is None


class TestWrongOtp:
    def _setup(self, monkeypatch):
        calls = []
        mock_auth(monkeypatch, OK_RESULT, calls)  # первый mock заменится ниже
        update, context = make_update(), FakeContext()
        context.user_data["vk_login_ctx"] = {"login": LOGIN, "password": PASSWORD,
                                             "attempts_code": 0}
        context.user_data["await_vk_code"] = True
        return calls, update, context

    def test_wrong_otp_allows_retry_up_to_limit(self, monkeypatch, db_isolated):
        _calls, update, context = self._setup(monkeypatch)
        mock_auth(monkeypatch, {"ok": False, "error": "wrong_otp", "code": 401}, _calls)

        run(set_vk_code_from_text(update, context, "0000"))
        assert "Осталось попыток: 2" in last_reply(update)
        assert context.user_data.get("await_vk_code")

        run(set_vk_code_from_text(update, context, "0001"))
        assert "Осталось попыток: 1" in last_reply(update)
        assert context.user_data.get("await_vk_code")

    def test_wrong_otp_resets_after_limit(self, monkeypatch, db_isolated):
        _calls, update, context = self._setup(monkeypatch)
        mock_auth(monkeypatch, {"ok": False, "error": "wrong_otp", "code": 401}, _calls)

        for code in ("0000", "0001", "0002"):
            run(set_vk_code_from_text(update, context, code))

        assert not any(context.user_data.get(k) for k in WAIT_STATE_KEYS)
        assert context.user_data.get("vk_login_ctx") is None
        assert "много попыток" in last_reply(update)


class TestNeedCaptcha:
    def test_need_captcha_sends_photo_and_sets_captcha_state(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, CAPTCHA_RESULT, calls)
        update, context = make_update(), FakeContext()
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))

        assert context.user_data.get("await_vk_captcha")
        assert update.message.photos == [CAPTCHA_URL]
        ctx = context.user_data.get("vk_login_ctx")
        assert ctx["captcha_sid"] == "captcha-sid-42"

    def test_captcha_context_persists_and_retries(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, CAPTCHA_RESULT, calls)
        update, context = make_update(), FakeContext()
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))

        # Второй вызов с captcha_key -> ok.
        mock_auth(monkeypatch, OK_RESULT, calls)
        monkeypatch.setattr(vk_flows, "vk_call", _mock_vk_call_ok())
        run(set_vk_captcha_from_text(update, context, "qwErtY12"))

        assert calls[1]["captcha_sid"] == "captcha-sid-42"
        assert calls[1]["captcha_key"] == "qwErtY12"
        assert db.get_vk_user_token(TG_ID) == TOKEN


class TestFinalErrors:
    @pytest.mark.parametrize("error,text_marker", [
        ("bad_password", "неверный логин или пароль"),
        ("too_much_tries", "много попыток"),
    ])
    def test_bad_password_and_too_much_tries_clear_state(self, monkeypatch, db_isolated,
                                                         error, text_marker):
        calls = []
        mock_auth(monkeypatch, {"ok": False, "error": error, "code": 401}, calls)
        update, context = make_update(), FakeContext()
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))

        assert not any(context.user_data.get(k) for k in WAIT_STATE_KEYS)
        assert context.user_data.get("vk_login_ctx") is None
        assert text_marker in last_reply(update)

    def test_network_error_keeps_state_for_retry(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, {"ok": False, "error": "network_error"}, calls)
        monkeypatch.setattr(config, "VK_LOGIN_ENABLED", True)

        # await_vk_login: состояние и ctx сохранены.
        update, context = make_update(), FakeContext()
        run(ask_vk_login(update, context))
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))
        assert context.user_data.get("await_vk_login")
        assert context.user_data.get("vk_login_ctx")["login"] == LOGIN
        assert "повтор" in last_reply(update).lower() or "ещё" in last_reply(update)

        # await_vk_code: состояние сохранено.
        context2 = FakeContext()
        context2.user_data["vk_login_ctx"] = {"login": LOGIN, "password": PASSWORD,
                                              "attempts_code": 0}
        context2.user_data["await_vk_code"] = True
        update2 = make_update()
        run(set_vk_code_from_text(update2, context2, "123456"))
        assert context2.user_data.get("await_vk_code")
        assert context2.user_data.get("vk_login_ctx") is not None

        # await_vk_captcha: состояние сохранено.
        context3 = FakeContext()
        context3.user_data["vk_login_ctx"] = {"login": LOGIN, "password": PASSWORD,
                                              "captcha_sid": "sid"}
        context3.user_data["await_vk_captcha"] = True
        update3 = make_update()
        run(set_vk_captcha_from_text(update3, context3, "abc"))
        assert context3.user_data.get("await_vk_captcha")

    def test_unknown_error_clears_state(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, {"ok": False, "error": "weird_vk_error", "code": 500}, calls)
        update, context = make_update(), FakeContext()
        run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))
        assert not any(context.user_data.get(k) for k in WAIT_STATE_KEYS)
        assert context.user_data.get("vk_login_ctx") is None

    def test_bad_format_keeps_state(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, OK_RESULT, calls)
        monkeypatch.setattr(config, "VK_LOGIN_ENABLED", True)
        update, context = make_update(), FakeContext()
        run(ask_vk_login(update, context))
        run(set_vk_login_from_text(update, context, "no-colon-here"))
        assert context.user_data.get("await_vk_login")
        assert calls == []  # auth не вызывался
        assert "Формат не распознан" in last_reply(update)


# --- Маршрутизация и сброс ---
class TestRoutingAndReset:
    def test_wait_state_keys_include_vk_states(self):
        assert "await_vk_login" in WAIT_STATE_KEYS
        assert "await_vk_code" in WAIT_STATE_KEYS
        assert "await_vk_captcha" in WAIT_STATE_KEYS

    def test_handle_text_button_routes_to_ask(self, monkeypatch):
        monkeypatch.setattr(config, "VK_LOGIN_ENABLED", True)
        update, context = make_update(BUTTON_VK_LOGIN), FakeContext()
        run(handle_text(update, context))
        assert context.user_data.get("await_vk_login")
        assert "login:password" in last_reply(update)

    def test_handle_text_routes_await_vk_login(self, monkeypatch, db_isolated):
        calls = []
        mock_auth(monkeypatch, {"ok": False, "error": "network_error"}, calls)
        monkeypatch.setattr(config, "VK_LOGIN_ENABLED", True)
        update, context = make_update(), FakeContext()
        run(ask_vk_login(update, context))

        routed = make_update(f"{LOGIN}:{PASSWORD}")
        run(handle_text(routed, context))
        assert len(calls) == 1

    def test_start_clears_vk_login_ctx(self):
        context = FakeContext()
        context.user_data["vk_login_ctx"] = {"login": LOGIN, "password": PASSWORD}
        context.user_data["await_vk_code"] = True
        update = make_update("/start")
        run(start_cmd(update, context))
        assert context.user_data.get("vk_login_ctx") is None
        assert not context.user_data.get("await_vk_code")

    def test_clear_ctx_helper(self):
        context = FakeContext()
        context.user_data["vk_login_ctx"] = {"login": "a", "password": "b"}
        context.user_data["await_vk_login"] = True
        _clear_vk_login_ctx(context)
        assert "vk_login_ctx" not in context.user_data
        assert not context.user_data.get("await_vk_login")


# --- §8.4 Секреты ---
class TestNoSecrets:
    def test_no_secrets_in_any_vk_login_text(self):
        import tg.messages as messages

        texts = [
            messages.vk_login_intro_text(),
            messages.vk_login_bad_format_text(),
            messages.vk_login_disabled_text(),
            messages.vk_need_code_text("+7***123"),
            messages.vk_need_captcha_text(),
            messages.vk_login_ok_text(),
            messages.vk_login_ok_with_warning_text("some reason"),
            messages.vk_bad_password_text(),
            messages.vk_wrong_otp_text(2),
            messages.vk_too_much_tries_text(),
            messages.vk_network_error_retry_text(),
            messages.vk_login_error_text(),
        ]
        for text in texts:
            serialized = repr(text)
            assert PASSWORD not in serialized
            assert TOKEN not in serialized
            assert LOGIN not in serialized

    def test_no_secrets_in_vk_flow_logs(self, monkeypatch, db_isolated, caplog):
        calls = []
        mock_auth(monkeypatch, OK_RESULT, calls)
        monkeypatch.setattr(vk_flows, "vk_call", _mock_vk_call_ok())
        update, context = make_update(), FakeContext()
        with caplog.at_level("DEBUG"):
            run(set_vk_login_from_text(update, context, f"{LOGIN}:{PASSWORD}"))
        assert PASSWORD not in caplog.text
        assert TOKEN not in caplog.text
        assert LOGIN not in caplog.text


# --- Моки vk_call (post-check); резолв имён — в момент вызова тестов ---
def _mock_vk_call_ok():
    async def fake_vk_call(method, params):
        return True, {"response": {"items": []}}, ""
    return fake_vk_call


def _mock_vk_call_fail():
    async def fake_vk_call(method, params):
        return False, {"error": {"error_code": 15, "error_msg": "Access denied"}}, \
            "VK error 15: Access denied"
    return fake_vk_call
