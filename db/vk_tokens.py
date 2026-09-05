"""Таблица vk_user_tokens: VK-токены и логин/пароли пользователей.

Значения хранятся только в зашифрованном виде (crypto.encrypt_str /
crypto.decrypt_str, формат 'enc:v1:...'). Секреты никогда не логируются.
"""
import logging
import sqlite3
import time

import config
from crypto import decrypt_str, encrypt_str

from .connection import DB_LOCK, conn, safe_int
from .settings import get_setting

logger = logging.getLogger(__name__)


def _vk_token_created_at(cur: sqlite3.Cursor, tg_id: int) -> int:
    """Сохраняет исходный created_at при перезаписи записи токена."""
    row = cur.execute("SELECT created_at FROM vk_user_tokens WHERE tg_id = ?", (tg_id,)).fetchone()
    if not row:
        return int(time.time())
    created = safe_int(row[0], 0)
    return created if created > 0 else int(time.time())


def save_vk_user_token(tg_id: int, plain_token: str) -> None:
    """Сохраняет пользовательский VK-токен (шифрует, kind='token')."""
    with DB_LOCK:
        cur = conn.cursor()
        created_at = _vk_token_created_at(cur, tg_id)
        cur.execute("INSERT OR REPLACE INTO vk_user_tokens (tg_id, token_enc, token_kind, "
                    "login_enc, password_enc, created_at, updated_at) "
                    "VALUES (?, ?, 'token', '', '', ?, ?)",
                    (tg_id, encrypt_str(plain_token), created_at, int(time.time())))
        conn.commit()


def save_vk_user_password(tg_id: int, login: str, password: str) -> None:
    """Сохраняет VK-логин/пароль (шифрует оба, kind='password')."""
    with DB_LOCK:
        cur = conn.cursor()
        created_at = _vk_token_created_at(cur, tg_id)
        cur.execute("INSERT OR REPLACE INTO vk_user_tokens (tg_id, token_enc, token_kind, "
                    "login_enc, password_enc, created_at, updated_at) "
                    "VALUES (?, '', 'password', ?, ?, ?, ?)",
                    (tg_id, encrypt_str(login), encrypt_str(password), created_at, int(time.time())))
        conn.commit()


def get_vk_user_token(tg_id: int) -> str | None:
    """Расшифрованный токен пользователя; None для kind='password' или отсутствия."""
    with DB_LOCK:
        row = conn.execute("SELECT token_enc, token_kind FROM vk_user_tokens WHERE tg_id = ?",
                           (tg_id,)).fetchone()
    if not row or row[1] != "token":
        return None
    return decrypt_str(row[0] or "") or None


def get_vk_user_credentials(tg_id: int) -> tuple[str, str] | None:
    """(login, password) расшифрованные; None для kind='token' или отсутствия."""
    with DB_LOCK:
        row = conn.execute("SELECT login_enc, password_enc, token_kind FROM vk_user_tokens "
                           "WHERE tg_id = ?", (tg_id,)).fetchone()
    if not row or row[2] != "password":
        return None
    login = decrypt_str(row[0] or "")
    password = decrypt_str(row[1] or "")
    if not login or not password:
        return None
    return login, password


def delete_vk_user_token(tg_id: int) -> None:
    """Удаляет сохранённые VK-учётные данные пользователя."""
    with DB_LOCK:
        conn.execute("DELETE FROM vk_user_tokens WHERE tg_id = ?", (tg_id,))
        conn.commit()


def get_any_active_vk_token() -> str | None:
    """Активный VK-токен: settings override → config fallback → первый user token."""
    override = get_setting("vk_token_override").strip()
    if override:
        return override
    fallback = config.VK_TOKEN.strip()
    if fallback:
        return fallback
    with DB_LOCK:
        row = conn.execute("SELECT token_enc FROM vk_user_tokens WHERE token_kind = 'token' "
                           "ORDER BY tg_id LIMIT 1").fetchone()
    return decrypt_str(row[0] or "") or None if row else None
