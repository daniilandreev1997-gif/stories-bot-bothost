"""Таблица instagram_settings: учётные данные и сессии instagrapi.

Пароль и settings-dump сессии хранятся зашифрованными (crypto, 'enc:v1:...').
verification_session — JSON-конверт {username, password_enc, challenge_context}
для незавершённого challenge-логина; pop_* забирает и очищает его.
"""
import json
import logging
import time

from crypto import decrypt_str, encrypt_str

from .connection import DB_LOCK, conn

logger = logging.getLogger(__name__)


def get_instagram_settings(tg_id: int) -> dict | None:
    """Все поля instagram_settings; password/session расшифрованы, None если нет."""
    with DB_LOCK:
        row = conn.execute("SELECT tg_id, username, password_enc, verification_session, "
                           "session_json_enc, session_valid, updated_at "
                           "FROM instagram_settings WHERE tg_id = ?", (tg_id,)).fetchone()
    if not row:
        return None
    return {"tg_id": row[0], "username": row[1] or None,
            "password": decrypt_str(row[2] or "") or None,
            "verification_session": row[3] or None,
            "session_json": decrypt_str(row[4] or "") or None,
            "session_valid": bool(row[5]), "updated_at": row[6]}


def save_instagram_credentials(tg_id: int, username: str, password: str) -> None:
    """Сохраняет IG-логин/пароль (пароль шифруется); сессию не трогает."""
    with DB_LOCK:
        conn.execute("INSERT INTO instagram_settings (tg_id, username, password_enc, "
                     "session_valid, updated_at) VALUES (?, ?, ?, 0, ?) "
                     "ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, "
                     "password_enc=excluded.password_enc, updated_at=excluded.updated_at",
                     (tg_id, username, encrypt_str(password), int(time.time())))
        conn.commit()


def save_instagram_session(tg_id: int, session_dict: dict) -> None:
    """Шифрует и сохраняет settings-dump instagrapi, помечает сессию валидной."""
    dump = json.dumps(session_dict, ensure_ascii=False)
    with DB_LOCK:
        conn.execute("INSERT INTO instagram_settings (tg_id, username, password_enc, "
                     "session_json_enc, session_valid, updated_at) VALUES (?, NULL, '', ?, 1, ?) "
                     "ON CONFLICT(tg_id) DO UPDATE SET session_json_enc=excluded.session_json_enc, "
                     "session_valid=1, updated_at=excluded.updated_at",
                     (tg_id, encrypt_str(dump), int(time.time())))
        conn.commit()


def set_instagram_session_invalid(tg_id: int) -> None:
    """Помечает IG-сессию невалидной (после login-required/challenge)."""
    with DB_LOCK:
        conn.execute("UPDATE instagram_settings SET session_valid = 0 WHERE tg_id = ?", (tg_id,))
        conn.commit()


def save_instagram_verification_session(tg_id: int, username: str, password: str,
                                        challenge_json: str | dict) -> None:
    """Сохраняет незавершённый challenge: пароль шифруется, verification_session =
    JSON {username, password_enc, challenge_context}."""
    challenge = (challenge_json if isinstance(challenge_json, str)
                 else json.dumps(challenge_json, ensure_ascii=False))
    payload = json.dumps({"username": username, "password_enc": encrypt_str(password),
                          "challenge_context": challenge}, ensure_ascii=False)
    with DB_LOCK:
        conn.execute("INSERT INTO instagram_settings (tg_id, username, password_enc, "
                     "verification_session, session_valid, updated_at) VALUES (?, ?, ?, ?, 0, ?) "
                     "ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, "
                     "password_enc=excluded.password_enc, "
                     "verification_session=excluded.verification_session, "
                     "updated_at=excluded.updated_at",
                     (tg_id, username, encrypt_str(password), payload, int(time.time())))
        conn.commit()


def pop_instagram_verification_session(tg_id: int) -> tuple[str, str, str] | None:
    """(username, password, challenge_json) и очистка поля; None если пусто/битые данные."""
    with DB_LOCK:
        row = conn.execute("SELECT verification_session FROM instagram_settings WHERE tg_id = ?",
                           (tg_id,)).fetchone()
        if not row or not row[0]:
            return None
        try:
            payload = json.loads(row[0])
            username = str(payload.get("username") or "")
            password = decrypt_str(payload.get("password_enc") or "")
            challenge = payload.get("challenge_context")
        except (ValueError, TypeError):
            logger.warning("instagram verification_session: битый JSON (tg_id=%s)", tg_id)
            username, password, challenge = "", "", None
        conn.execute("UPDATE instagram_settings SET verification_session = NULL WHERE tg_id = ?",
                     (tg_id,))
        conn.commit()
    challenge_str = (challenge if isinstance(challenge, str)
                     else (json.dumps(challenge, ensure_ascii=False)
                           if challenge is not None else ""))
    if not username or not password:
        return None
    return username, password, challenge_str
