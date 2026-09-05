"""Тесты crypto.py: roundtrip, legacy-plaintext, try_decrypt_str, битый токен."""
from crypto import PREFIX, decrypt_str, encrypt_str, try_decrypt_str


class TestEncryptDecryptRoundtrip:
    def test_roundtrip(self):
        plain = "секрет-значение-123"
        cipher = encrypt_str(plain)
        assert cipher.startswith(PREFIX)
        assert plain not in cipher
        assert decrypt_str(cipher) == plain

    def test_empty_string(self):
        assert encrypt_str("") == ""
        assert decrypt_str("") == ""

    def test_legacy_plaintext_passthrough(self):
        assert decrypt_str("plain-old-token") == "plain-old-token"

    def test_unicode_roundtrip(self):
        plain = "пароль 🔐 한국어"
        assert decrypt_str(encrypt_str(plain)) == plain


class TestTryDecryptStr:
    def test_encrypted_value(self):
        plain = "value-42"
        ok, value = try_decrypt_str(encrypt_str(plain))
        assert ok is True
        assert value == plain

    def test_broken_encrypted_value(self):
        ok, value = try_decrypt_str(PREFIX + "not-a-valid-fernet-token")
        assert ok is False
        assert value == ""

    def test_legacy_plaintext(self):
        ok, value = try_decrypt_str("legacy-open-text")
        assert ok is False
        assert value == "legacy-open-text"


class TestBrokenFernet:
    def test_decrypt_broken_returns_empty_not_raise(self):
        # Битый fernet-токен после префикса -> '' без исключения
        assert decrypt_str(PREFIX + "%%%broken%%%") == ""

    def test_decrypt_wrong_key_payload_returns_empty(self):
        # Случайная base64-строка не является валидным Fernet-токеном
        assert decrypt_str(PREFIX + "QUJDREVGR0hJSktMTU5PUA==") == ""
