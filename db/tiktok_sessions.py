"""Таблицы tiktok_sessions и tiktok_stats: сессии и кэш статистики TikTok.

sessionid, cookies_json, login (email/username) и password хранятся
зашифрованными (crypto, 'enc:v1:...'). Логин/пароль используются
tiktok.login (playwright-вход), сессии — yt-dlp cookiefile.
Статистика (followers/hearts/video_count) — не секрет, хранится открыто.
"""
import logging
import time

from crypto import decrypt_str, encrypt_str

from .connection import DB_LOCK, conn, safe_int

logger = logging.getLogger(__name__)


def save_tiktok_login(tg_id: int, email: str, password: str) -> None:
    """Сохраняет login (email/username) + password шифрованно (UPSERT login-колонок).

    Обновляет ТОЛЬКО login_enc/password_enc (+ updated_at, session_valid=0 —
    новая попытка входа инвалидирует прежнюю сессию); sessionid/cookies
    колонки не трогаются. Строки нет -> INSERT.
    """
    with DB_LOCK:
        conn.execute(
            "INSERT INTO tiktok_sessions "
            "(tg_id, login_enc, password_enc, session_valid, updated_at) "
            "VALUES (?, ?, ?, 0, ?) "
            "ON CONFLICT(tg_id) DO UPDATE SET login_enc=excluded.login_enc, "
            "password_enc=excluded.password_enc, session_valid=0, "
            "updated_at=excluded.updated_at",
            (tg_id, encrypt_str(email), encrypt_str(password), int(time.time())),
        )
        conn.commit()


def get_tiktok_login(tg_id: int) -> tuple[str, str] | None:
    """(email, password) расшифрованные; None если нет/пусто."""
    with DB_LOCK:
        row = conn.execute("SELECT login_enc, password_enc FROM tiktok_sessions "
                           "WHERE tg_id = ?", (tg_id,)).fetchone()
    if not row:
        return None
    login = decrypt_str(row[0] or "")
    password = decrypt_str(row[1] or "")
    if not login or not password:
        return None
    return login, password


def save_tiktok_session(tg_id: int, sessionid: str, cookies_json: str) -> None:
    """Шифрует и сохраняет sessionid + cookies JSON, помечает сессию валидной."""
    with DB_LOCK:
        conn.execute("INSERT INTO tiktok_sessions (tg_id, sessionid_enc, cookies_json_enc, "
                     "session_valid, updated_at) VALUES (?, ?, ?, 1, ?) "
                     "ON CONFLICT(tg_id) DO UPDATE SET sessionid_enc=excluded.sessionid_enc, "
                     "cookies_json_enc=excluded.cookies_json_enc, session_valid=1, "
                     "updated_at=excluded.updated_at",
                     (tg_id, encrypt_str(sessionid), encrypt_str(cookies_json), int(time.time())))
        conn.commit()


def get_tiktok_session(tg_id: int) -> tuple[str, str] | None:
    """(sessionid, cookies_json) расшифрованные; None если нет/пусто."""
    with DB_LOCK:
        row = conn.execute("SELECT sessionid_enc, cookies_json_enc FROM tiktok_sessions "
                           "WHERE tg_id = ?", (tg_id,)).fetchone()
    if not row:
        return None
    sessionid = decrypt_str(row[0] or "")
    cookies_json = decrypt_str(row[1] or "")
    if not sessionid or not cookies_json:
        return None
    return sessionid, cookies_json


def set_tiktok_session_invalid(tg_id: int) -> None:
    """Помечает TikTok-сессию невалидной."""
    with DB_LOCK:
        conn.execute("UPDATE tiktok_sessions SET session_valid = 0 WHERE tg_id = ?", (tg_id,))
        conn.commit()


def upsert_tiktok_stats(tg_id: int, username: str, followers: int, hearts: int,
                        video_count: int, fetched_at: int) -> None:
    """Кэширует статистику TikTok-аккаунта (followers/hearts/videos)."""
    with DB_LOCK:
        conn.execute("INSERT OR REPLACE INTO tiktok_stats (tg_id, username, followers, hearts, "
                     "video_count, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (tg_id, username, followers, hearts, video_count, safe_int(fetched_at, 0)))
        conn.commit()


def get_tiktok_stats(tg_id: int, username: str) -> dict | None:
    """Кэшированная статистика аккаунта или None."""
    with DB_LOCK:
        row = conn.execute("SELECT tg_id, username, followers, hearts, video_count, fetched_at "
                           "FROM tiktok_stats WHERE tg_id = ? AND username = ?",
                           (tg_id, username)).fetchone()
    if not row:
        return None
    return {"tg_id": row[0], "username": row[1], "followers": row[2], "hearts": row[3],
            "video_count": row[4], "fetched_at": row[5]}
