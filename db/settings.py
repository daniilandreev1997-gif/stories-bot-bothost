"""Таблица settings: произвольные key/value-настройки бота.

Значения хранятся открытым текстом — секреты сюда складывать нельзя
(VK-токены/пароли/сессии шифруются через crypto, см. vk_tokens/instagram).
"""
import logging

from .connection import DB_LOCK, conn

logger = logging.getLogger(__name__)


def get_setting(key: str) -> str:
    """Значение settings по ключу; '' если нет/пусто."""
    with DB_LOCK:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row and row[0] else ""


def set_setting(key: str, value: str) -> None:
    """Вставляет/заменяет значение в settings."""
    with DB_LOCK:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
