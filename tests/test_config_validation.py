"""Тесты валидации STORIES_ENCRYPTION_KEY при импорте config (диагностика crash-loop).

config.py валидирует ключ ПРИ импорте модуля, поэтому тесты перезагружают его
через ``importlib.reload`` с подменённой переменной окружения. Фикстура
``reload_config`` гарантирует восстановление env и повторную перезагрузку после
каждого теста, чтобы остальные тесты продолжали видеть исходный валидный ключ
из conftest.

Секреты не логируются: сообщение об ошибке содержит только длину значения и
флаг плейсхолдера — это проверяется отдельным тестом на «не-утечку».
"""
import importlib
import os

import pytest
from cryptography.fernet import Fernet

import config


@pytest.fixture()
def reload_config():
    """Перезагрузка config с произвольным env + полное восстановление после теста.

    conftest.py выставляет API_TOKEN/STORIES_ENCRYPTION_KEY ДО импорта config;
    фикстура сохраняет эти значения, возвращает их в os.environ после теста и
    перезагружает config, чтобы состояние модуля вернулось к исходному.
    """
    keys = ("API_TOKEN", "STORIES_ENCRYPTION_KEY", "DB_PATH", "INSTAGRAM_VIEWER_BASE_URL")
    saved = {key: os.environ.get(key) for key in keys}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(config)


class TestEncryptionKeyValidation:
    """Валидация STORIES_ENCRYPTION_KEY при импорте config (raise при бите)."""

    def test_empty_key_raises_with_len_zero(self, reload_config):
        """Пустой ключ: ValueError с 'не является валидным Fernet-ключом' и 'получено символов: 0'."""
        os.environ["STORIES_ENCRYPTION_KEY"] = ""
        with pytest.raises(ValueError) as excinfo:
            importlib.reload(config)
        message = str(excinfo.value)
        assert "не является валидным Fernet-ключом" in message
        assert "получено символов: 0" in message
        assert "looks_like_placeholder=False" in message
        assert "значение не логируется" in message
        assert "Fernet.generate_key().decode()" in message  # подсказка с командой генерации

    def test_replace_me_placeholder_flag_true(self, reload_config):
        """Плейсхолдер replace_me: ValueError содержит looks_like_placeholder=True."""
        os.environ["STORIES_ENCRYPTION_KEY"] = "replace_me"
        with pytest.raises(ValueError) as excinfo:
            importlib.reload(config)
        message = str(excinfo.value)
        assert "не является валидным Fernet-ключом" in message
        assert "получено символов: 10" in message
        assert "looks_like_placeholder=True" in message

    def test_error_does_not_leak_value(self, reload_config):
        """Произвольное невалидное значение НЕ попадает в текст ошибки (только длина/флаг)."""
        invalid_value = "s3cr3t-NOT-a-real-fernet-key-value"
        os.environ["STORIES_ENCRYPTION_KEY"] = invalid_value
        with pytest.raises(ValueError) as excinfo:
            importlib.reload(config)
        message = str(excinfo.value)
        assert invalid_value not in message
        assert f"получено символов: {len(invalid_value)}" in message
        assert "looks_like_placeholder=False" in message

    def test_valid_key_imports_and_roundtrip(self, reload_config):
        """Валидный ключ: config импортируется без ошибки, FERNET шифрует/расшифровывает."""
        os.environ["STORIES_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
        importlib.reload(config)  # не должен поднять
        plaintext = b"config-validation-roundtrip"
        token = config.FERNET.encrypt(plaintext)
        assert config.FERNET.decrypt(token) == plaintext


class TestVkAppEnvVars:
    """VK_APP_ID / VK_SERVICE_KEY / VK_SECURE_KEY: env -> поля, unset -> пустые строки.

    Фикс бага №1: креды приложения (VK_APP_ID/VK_SECURE_KEY) используются как
    дефолт direct-auth вместо публичных Android-констант; VK_SERVICE_KEY —
    последняя fallback-ступень токенов (с явной деградацией, не тихий []).
    """

    def test_config_vk_app_env_vars(self, reload_config):
        """env задан -> поля заполнены; unset -> пустые строки."""
        os.environ["VK_APP_ID"] = "54425853"
        os.environ["VK_SERVICE_KEY"] = "service-key-env-value"
        os.environ["VK_SECURE_KEY"] = "secure-key-env-value"
        try:
            importlib.reload(config)
            assert config.VK_APP_ID == "54425853"
            assert config.VK_SERVICE_KEY == "service-key-env-value"
            assert config.VK_SECURE_KEY == "secure-key-env-value"
        finally:
            os.environ.pop("VK_APP_ID", None)
            os.environ.pop("VK_SERVICE_KEY", None)
            os.environ.pop("VK_SECURE_KEY", None)
            importlib.reload(config)
            assert config.VK_APP_ID == ""
            assert config.VK_SERVICE_KEY == ""
            assert config.VK_SECURE_KEY == ""

    def test_direct_auth_defaults_prefer_app_credentials(self, reload_config):
        """VK_APP_ID+VK_SECURE_KEY заданы, VK_DIRECT_AUTH_* нет -> дефолты direct-auth = креды приложения.

        Иначе (креды приложения пусты) — прежние публичные Android-дефолты
        (совместимость с test_vk_login_flow.py::TestGateEnvDefaults).
        """
        # Кейс 1: VK_APP_ID+VK_SECURE_KEY заданы -> они становятся дефолтами direct-auth.
        os.environ["VK_APP_ID"] = "app-777"
        os.environ["VK_SECURE_KEY"] = "app-secret-777"
        os.environ.pop("VK_DIRECT_AUTH_CLIENT_ID", None)
        os.environ.pop("VK_DIRECT_AUTH_CLIENT_SECRET", None)
        try:
            importlib.reload(config)
            assert config.VK_DIRECT_AUTH_CLIENT_ID == "app-777"
            assert config.VK_DIRECT_AUTH_CLIENT_SECRET == "app-secret-777"
        finally:
            os.environ.pop("VK_APP_ID", None)
            os.environ.pop("VK_SECURE_KEY", None)

        # Кейс 2: креды приложения пусты -> прежние публичные Android-дефолты.
        os.environ.pop("VK_DIRECT_AUTH_CLIENT_ID", None)
        os.environ.pop("VK_DIRECT_AUTH_CLIENT_SECRET", None)
        importlib.reload(config)
        assert config.VK_DIRECT_AUTH_CLIENT_ID == "2274003"
        assert config.VK_DIRECT_AUTH_CLIENT_SECRET == "hHbZxrka2uZ6jB1inYsH"
