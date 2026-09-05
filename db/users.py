"""Таблица users: пользователи бота, привязки VK/TikTok и состояние синхронизации.

ensure_user_row вызывается внутри сессий, уже держащих DB_LOCK (см. докстринг).
"""
import logging
import sqlite3

from .connection import DB_LOCK, conn, safe_int

logger = logging.getLogger(__name__)


def ensure_user_row(cur: sqlite3.Cursor, tg_id: int) -> None:
    """Создаёт строку users при отсутствии (вызывается внутри with DB_LOCK)."""
    cur.execute("SELECT tg_id FROM users WHERE tg_id = ?", (tg_id,))
    if not cur.fetchone():
        cur.execute("INSERT INTO users (tg_id, vk_id, last_story_id, tiktok_username, "
                    "tiktok_initial_sync_done, tiktok_last_dispatch_ts) VALUES (?, ?, ?, ?, ?, ?)",
                    (tg_id, None, None, None, 0, 0))


def load_vk_users() -> list[tuple[int, str, str | None]]:
    """[(tg_id, vk_id, last_story_id)] всех пользователей с заданным vk_id."""
    with DB_LOCK:
        return conn.execute("SELECT tg_id, vk_id, last_story_id FROM users "
                            "WHERE vk_id IS NOT NULL AND TRIM(vk_id) != ''").fetchall()


def load_tiktok_users() -> list[tuple[int, str]]:
    """[(tg_id, tiktok_username)] всех пользователей с заданным TikTok-ником."""
    with DB_LOCK:
        return conn.execute("SELECT tg_id, tiktok_username FROM users "
                            "WHERE tiktok_username IS NOT NULL AND TRIM(tiktok_username) != ''"
                            ).fetchall()


def save_user_vk_id(tg_id: int, vk_id: str) -> None:
    """Сохраняет vk_id и сбрасывает last_story_id (новый аккаунт — с начала)."""
    with DB_LOCK:
        cur = conn.cursor()
        ensure_user_row(cur, tg_id)
        cur.execute("UPDATE users SET vk_id = ?, last_story_id = NULL WHERE tg_id = ?",
                    (vk_id, tg_id))
        conn.commit()


def save_user_tiktok_username(tg_id: int, username: str) -> None:
    """Сохраняет отслеживаемый TikTok-username пользователя."""
    with DB_LOCK:
        cur = conn.cursor()
        ensure_user_row(cur, tg_id)
        cur.execute("UPDATE users SET tiktok_username = ? WHERE tg_id = ?", (username, tg_id))
        conn.commit()


def reset_tiktok_sync_state(tg_id: int) -> None:
    """Сбрасывает состояние initial sync и время последней отправки."""
    with DB_LOCK:
        cur = conn.cursor()
        ensure_user_row(cur, tg_id)
        cur.execute("UPDATE users SET tiktok_initial_sync_done = 0, tiktok_last_dispatch_ts = 0 "
                    "WHERE tg_id = ?", (tg_id,))
        conn.commit()


def get_tiktok_sync_state(tg_id: int) -> tuple[bool, int]:
    """(initial_sync_done, last_dispatch_ts) для пользователя."""
    with DB_LOCK:
        cur = conn.cursor()
        ensure_user_row(cur, tg_id)
        row = cur.execute("SELECT tiktok_initial_sync_done, tiktok_last_dispatch_ts "
                          "FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
    if not row:
        return False, 0
    return bool(safe_int(row[0], 0)), safe_int(row[1], 0)


def set_tiktok_sync_state(tg_id: int, *, initial_sync_done: bool | None = None,
                          last_dispatch_ts: int | None = None) -> None:
    """Точечно обновляет поля синхронизации (None = не менять)."""
    updates, params = [], []
    if initial_sync_done is not None:
        updates.append("tiktok_initial_sync_done = ?")
        params.append(1 if initial_sync_done else 0)
    if last_dispatch_ts is not None:
        updates.append("tiktok_last_dispatch_ts = ?")
        params.append(safe_int(last_dispatch_ts, 0))
    if not updates:
        return
    with DB_LOCK:
        cur = conn.cursor()
        ensure_user_row(cur, tg_id)
        params.append(tg_id)
        cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE tg_id = ?", tuple(params))
        conn.commit()


def update_last_story_id(tg_id: int, last_story_id: str) -> None:
    """Обновляет курсор последнего просмотренного VK-стори."""
    with DB_LOCK:
        conn.execute("UPDATE users SET last_story_id = ? WHERE tg_id = ?", (last_story_id, tg_id))
        conn.commit()


def get_user_vk_id(tg_id: int) -> str | None:
    """vk_id пользователя или None."""
    with DB_LOCK:
        row = conn.execute("SELECT vk_id FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
    return row[0] if row and row[0] else None


def get_user_tiktok_username(tg_id: int) -> str | None:
    """TikTok-username пользователя или None."""
    with DB_LOCK:
        row = conn.execute("SELECT tiktok_username FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
    return row[0] if row and row[0] else None
