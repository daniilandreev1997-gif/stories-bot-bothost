"""Соединение с SQLite: единый объект conn, блокировка DB_LOCK, PRAGMA.

Один sqlite3.Connection (check_same_thread=False) на весь процесс; доступ
сериализуется через DB_LOCK (RLock, допускает вложенный захват).

ВАЖНО: прагмы НЕ применяются при импорте этого модуля — инициализация
(_set_pragmas + миграции) выполняется в db/__init__.py после сборки пакета,
как и в старом монолитном db.py.
"""
import logging
import sqlite3
import threading

import config

logger = logging.getLogger(__name__)

BUSY_TIMEOUT_MS = 5000
# RLock: допускает вложенный захват из функций этого же пакета.
DB_LOCK = threading.RLock()

conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)


def safe_int(value, default: int = 0) -> int:
    """int(value) без исключений; при ошибке — default (копия bot_host.safe_int)."""
    try:
        return int(value)
    except Exception:
        return default


def _set_pragmas() -> None:
    """Настраивает PRAGMA соединения (WAL, busy_timeout, FK, synchronous)."""
    with DB_LOCK:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS};")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA synchronous=NORMAL;")


def close() -> None:
    """Идемпотентно закрывает соединение при остановке приложения."""
    with DB_LOCK:
        try:
            conn.commit()
        except sqlite3.Error:
            pass
        try:
            conn.close()
        except sqlite3.Error:
            # Повторный вызов после закрытия sqlite-соединения безопасен.
            pass
