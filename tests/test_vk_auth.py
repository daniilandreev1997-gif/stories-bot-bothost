"""Тесты vk.auth.vk_direct_auth против мока requests (подзадача A3).

Мокается requests.post (как в фактической реализации vk/auth.py).
Все ветки: success, bad_password (invalid_client / HTTP 401),
need_validation (2FA), need_captcha (VK error 17), too_much_tries (406),
wrong_otp, network_error. Плюс проверка, что секреты не попадают в результат.
"""
import asyncio
import json

import pytest

import config
import vk.auth
from vk.auth import vk_direct_auth

AUTH_KWARGS = {
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
    "scope": "stories",
}
LOGIN = "test-user-login"
PASSWORD = "test-user-password-SUPER-secret"


class TestDefaultCredentials:
    """Без явных кредов vk_direct_auth берёт значения из config (раунд 3).

    Дефолт — публичные креды официального VK Android-клиента (2274003);
    это публичные константы, не пользовательский секрет.
    """

    def test_defaults_come_from_config(self, captured):
        captured["response"] = FakeResponse({"access_token": "tok-123", "user_id": 1})
        result = asyncio.run(vk_direct_auth(LOGIN, PASSWORD))
        assert result["ok"] is True
        assert captured["data"]["client_id"] == config.VK_DIRECT_AUTH_CLIENT_ID
        assert captured["data"]["client_secret"] == config.VK_DIRECT_AUTH_CLIENT_SECRET
        assert captured["data"]["client_id"] == "2274003"
        assert captured["data"]["client_secret"] == "hHbZxrka2uZ6jB1inYsH"
        assert captured["data"]["grant_type"] == "password"


class FakeResponse:
    """Минимальный мок requests.Response (.json() + .status_code)."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.fixture()
def captured(monkeypatch):
    """Подменяет vk.auth.requests.post, возвращает (payload|None, response_factory)."""
    state = {"data": None, "url": None}

    def fake_post(url, data=None, timeout=None):
        state["data"] = dict(data or {})
        state["url"] = url
        return state.get("response")

    monkeypatch.setattr(vk.auth.requests, "post", fake_post)
    return state


def run_auth(**kwargs):
    return asyncio.run(
        vk_direct_auth(LOGIN, PASSWORD, **{**AUTH_KWARGS, **kwargs})
    )


class TestSuccess:
    def test_access_token_and_user_id(self, captured):
        captured["response"] = FakeResponse({"access_token": "tok-123", "user_id": 456789})
        result = run_auth()
        assert result == {"ok": True, "access_token": "tok-123", "user_id": "456789"}
        # Убеждается, что запрос ушёл на VK OAuth с grant_type=password
        assert captured["url"] == vk.auth.VK_OAUTH_TOKEN_URL
        assert captured["data"]["grant_type"] == "password"
        assert captured["data"]["username"] == LOGIN
        assert captured["data"]["password"] == PASSWORD


class TestBadPassword:
    def test_invalid_client_error(self, captured):
        captured["response"] = FakeResponse(
            {"error": "invalid_client",
             "error_description": "bad_password: incorrect user or password"},
            status_code=401,
        )
        result = run_auth()
        assert result["ok"] is False
        assert result["error"] == "bad_password"
        assert result["code"] == 401

    def test_http_401_without_error_field(self, captured):
        captured["response"] = FakeResponse({}, status_code=401)
        result = run_auth()
        assert result["ok"] is False
        assert result["error"] == "bad_password"
        assert result["code"] == 401


class TestNeedValidation:
    def test_2fa_without_code(self, captured):
        captured["response"] = FakeResponse(
            {"error": "need_validation",
             "error_description": "need_validation",
             "validation_type": "2fa_sms",
             "validation_sid": "sid-777",
             "phone_mask": "+7***123"},
            status_code=401,
        )
        result = run_auth()  # code не передан
        assert result["ok"] is False
        assert result["error"] == "need_validation"
        assert result["need_validation"] is True
        assert result["validation_sid"] == "sid-777"
        assert result["phone_mask"] == "+7***123"


class TestNeedCaptcha:
    def test_vk_error_17(self, captured):
        captured["response"] = FakeResponse(
            {"error": "need_captcha",
             "error_description": "captcha needed",
             "captcha_sid": "captcha-sid-42",
             "captcha_img": "https://api.vk.com/captcha.jpg"},
            status_code=401,
        )
        result = run_auth()
        assert result["ok"] is False
        assert result["error"] == "need_captcha"
        assert result["code"] == 17
        assert result["need_captcha"] is True
        assert result["captcha_sid"] == "captcha-sid-42"
        assert result["captcha_img"] == "https://api.vk.com/captcha.jpg"

    def test_captcha_by_sid_only(self, captured):
        # Фактическая логика: captcha_sid в ответе также трактуется как need_captcha
        captured["response"] = FakeResponse({"captcha_sid": "sid-only"}, status_code=200)
        result = run_auth()
        assert result["error"] == "need_captcha"
        assert result["captcha_sid"] == "sid-only"


class TestTooMuchTries:
    def test_error_field(self, captured):
        captured["response"] = FakeResponse(
            {"error": "too_much_tries", "error_description": "Too many attempts"},
            status_code=406,
        )
        result = run_auth()
        assert result["ok"] is False
        assert result["error"] == "too_much_tries"
        assert result["code"] == 406

    def test_http_406_without_error_field(self, captured):
        captured["response"] = FakeResponse({}, status_code=406)
        result = run_auth()
        assert result["error"] == "too_much_tries"


class TestWrongOtp:
    def test_code_with_invalid_client(self, captured):
        captured["response"] = FakeResponse(
            {"error": "invalid_client", "error_description": "wrong code"},
            status_code=401,
        )
        result = run_auth(code="0000")
        assert result["ok"] is False
        assert result["error"] == "wrong_otp"
        assert result["code"] == 401


class TestNetworkError:
    def test_transport_failure(self, monkeypatch):
        def boom(url, data=None, timeout=None):
            raise ConnectionError("no route to host")

        monkeypatch.setattr(vk.auth.requests, "post", boom)
        result = run_auth()
        assert result["ok"] is False
        assert result["error"] == "network_error"


class TestNoSecretsInResult:
    @pytest.mark.parametrize(
        "payload,status",
        [
            ({"error": "invalid_client", "error_description": "bad_password"}, 401),
            ({"error": "need_validation", "validation_sid": "s"}, 401),
            ({"error": "need_captcha", "captcha_sid": "cs", "captcha_img": "i"}, 401),
            ({"error": "too_much_tries"}, 406),
            ({}, 500),
        ],
    )
    def test_error_result_without_secrets(self, captured, payload, status):
        captured["response"] = FakeResponse(payload, status_code=status)
        result = run_auth()
        serialized = json.dumps(result, ensure_ascii=False)
        assert result["ok"] is False
        assert PASSWORD not in serialized
        assert LOGIN not in serialized
        assert AUTH_KWARGS["client_secret"] not in serialized
