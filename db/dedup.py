"""Таблица tiktok_sent: дедупликация отправленных TikTok-постов.

PRIMARY KEY (tg_id, post_id) гарантирует, что один пост не уйдёт пользователю
дважды; mark_tiktok_post_sent идемпотентна (INSERT OR IGNORE).
"""
import logging
import time

from .connection import DB_LOCK, conn

logger = logging.getLogger(__name__)


def get_tiktok_sent_ids(tg_id: int) -> set[str]:
    """Множество уже отправленных пользователю TikTok post_id."""
    with DB_LOCK:
        rows = conn.execute("SELECT post_id FROM tiktok_sent WHERE tg_id = ?", (tg_id,)).fetchall()
    return {row[0] for row in rows}


def mark_tiktok_post_sent(tg_id: int, post_id: str) -> None:
    """Отмечает пост отправленным (INSERT OR IGNORE — повтор безопасен)."""
    with DB_LOCK:
        conn.execute("INSERT OR IGNORE INTO tiktok_sent (tg_id, post_id, sent_at) VALUES (?, ?, ?)",
                     (tg_id, post_id, int(time.time())))
        conn.commit()


def clear_tiktok_sent_for_user(tg_id: int) -> None:
    """Очищает историю отправленных постов (для сброса командой)."""
    with DB_LOCK:
        conn.execute("DELETE FROM tiktok_sent WHERE tg_id = ?", (tg_id,))
        conn.commit()
