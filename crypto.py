"""Шифрование пользовательских секретов (VK-токены, пароли, sessionid).

Формат зашифрованной строки: ``enc:v1:<fernet-token>``.
Ключ единый для всех записей — берётся из ``config.FERNET`` (задаётся
``STORIES_ENCRYPTION_KEY`` в ``.env`` рядом с ботом).

Обратная совместимость со старыми данными (legacy): строки без префикса
``enc:v1:`` считаются записанными открытым текстом (как старое
``settings.vk_token_override``) и возвращаются без изменений.
Ошибки расшифровки не роняют бота: warning в лог + пустая строка.
"""

import logging

import config
from cryptography.fernet import InvalidToken

logger = logging.getLogger(__name__)

# Префикс версии формата шифрования.
PREFIX = "enc:v1:"


def _decrypt_fernet(payload: str) -> str:
    """Расшифровывает Fernet-токен; при любой ошибке — warning и ''."""
    try:
        return config.FERNET.decrypt(payload.encode()).decode()
    except (InvalidToken, Exception) as exc:  # InvalidToken, binascii.Error и др.
        logger.warning("Не удалось расшифровать значение (len=%d): %s", len(payload), exc)
        return ""


def encrypt_str(plain: str) -> str:
    """Шифрует строку: '' -> '', иначе 'enc:v1:<fernet-token>'."""
    if not plain:
        return ""
    return PREFIX + config.FERNET.encrypt(plain.encode()).decode()


def decrypt_str(cipher: str) -> str:
    """Расшифровывает строку для использования.

    - '' -> '';
    - 'enc:v1:...' -> расшифрованное значение (ошибка -> '' + warning);
    - без префикса -> возвращается как есть (legacy открытым текстом).
    """
    if not cipher:
        return ""
    if cipher.startswith(PREFIX):
        return _decrypt_fernet(cipher[len(PREFIX):])
    return cipher  # legacy: записано открытым текстом


def try_decrypt_str(cipher: str) -> tuple[bool, str]:
    """Пытается расшифровать строку, сообщая, была ли она шифрованной.

    Возвращает:
    - (True, значение) — 'enc:v1:'-строка успешно расшифрована;
    - (False, '') — 'enc:v1:'-строка, но расшифровать не удалось;
    - (False, cipher) — строка не была зашифрована (legacy plaintext).
    """
    if not cipher.startswith(PREFIX):
        return False, cipher
    token = cipher[len(PREFIX):]
    try:
        return True, config.FERNET.decrypt(token.encode()).decode()
    except (InvalidToken, Exception) as exc:  # InvalidToken, binascii.Error и др.
        logger.warning("Не удалось расшифровать значение (len=%d): %s", len(token), exc)
        return False, ""
