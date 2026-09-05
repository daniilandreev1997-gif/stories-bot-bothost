"""Версионирование схемы БД: PRAGMA user_version + список MIGRATIONS.

run_migrations() при импорте пакета db (см. db/__init__.py) — замена
ensure_schema() из bot_host.py. Старые БД без user_version (v0) подхватывает
миграция 1: CREATE TABLE IF NOT EXISTS + idempotent ALTER'ы.
"""
import logging
import sqlite3

from .connection import DB_LOCK, conn

logger = logging.getLogger(__name__)


def get_user_version() -> int:
    """Текущая версия схемы БД (PRAGMA user_version)."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def set_user_version(cur: sqlite3.Cursor, v: int) -> None:
    """Записывает версию схемы; фиксируется conn.commit() вызывающей стороны."""
    cur.execute(f"PRAGMA user_version = {int(v)}")


# ======================= MIGRATIONS =======================
SQL_USERS = ("CREATE TABLE IF NOT EXISTS users (tg_id INTEGER PRIMARY KEY, vk_id TEXT, "
             "last_story_id TEXT, tiktok_username TEXT, tiktok_initial_sync_done INTEGER DEFAULT 0, "
             "tiktok_last_dispatch_ts INTEGER DEFAULT 0)")
SQL_SETTINGS = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
SQL_TIKTOK_SENT = ("CREATE TABLE IF NOT EXISTS tiktok_sent (tg_id INTEGER NOT NULL, "
                   "post_id TEXT NOT NULL, sent_at INTEGER NOT NULL, PRIMARY KEY (tg_id, post_id))")
SQL_TIKTOK_POST_CLAIMS = ("CREATE TABLE IF NOT EXISTS tiktok_post_claims (tg_id INTEGER NOT NULL, "
                          "post_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'claimed', "
                          "claimed_at INTEGER NOT NULL, updated_at INTEGER, "
                          "attempts INTEGER NOT NULL DEFAULT 0, reason TEXT, "
                          "PRIMARY KEY (tg_id, post_id))")


def _table_columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    """Имена колонок таблицы через PRAGMA table_info (table — наша константа)."""
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _migration_v1(cur: sqlite3.Cursor) -> None:
    """v0→1: базовые users/settings/tiktok_sent + tiktok-колонки users (ALTER idempotent)."""
    cur.execute(SQL_USERS)
    cur.execute(SQL_SETTINGS)
    cur.execute(SQL_TIKTOK_SENT)
    cols = _table_columns(cur, "users")
    if "tiktok_username" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN tiktok_username TEXT")
    if "tiktok_initial_sync_done" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN tiktok_initial_sync_done INTEGER DEFAULT 0")
    if "tiktok_last_dispatch_ts" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN tiktok_last_dispatch_ts INTEGER DEFAULT 0")


def _migration_v2(cur: sqlite3.Cursor) -> None:
    """v1→2: vk_user_tokens. settings.vk_token_override НЕ переносится (глобальный override бота)."""
    cur.execute("CREATE TABLE IF NOT EXISTS vk_user_tokens (tg_id INTEGER PRIMARY KEY, "
                "token_enc TEXT NOT NULL, token_kind TEXT NOT NULL DEFAULT 'token', "
                "login_enc TEXT, password_enc TEXT, created_at INTEGER NOT NULL, "
                "updated_at INTEGER NOT NULL)")


def _migration_v3(cur: sqlite3.Cursor) -> None:
    """v2→3: instagram_settings — учётные данные и сессия instagrapi."""
    cur.execute("CREATE TABLE IF NOT EXISTS instagram_settings (tg_id INTEGER PRIMARY KEY, "
                "username TEXT, password_enc TEXT, verification_session TEXT, "
                "session_json_enc TEXT, session_valid INTEGER NOT NULL DEFAULT 0, "
                "updated_at INTEGER NOT NULL)")


def _migration_v4(cur: sqlite3.Cursor) -> None:
    """v3→4: tiktok_stats (кэш статистики) и tiktok_sessions (sessionid/cookies)."""
    cur.execute("CREATE TABLE IF NOT EXISTS tiktok_stats (tg_id INTEGER NOT NULL, "
                "username TEXT NOT NULL, followers INTEGER, hearts INTEGER, video_count INTEGER, "
                "fetched_at INTEGER NOT NULL, PRIMARY KEY (tg_id, username))")
    cur.execute("CREATE TABLE IF NOT EXISTS tiktok_sessions (tg_id INTEGER PRIMARY KEY, "
                "sessionid_enc TEXT, cookies_json_enc TEXT, "
                "session_valid INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL)")


def _migration_v5(cur: sqlite3.Cursor) -> None:
    """v4→5: индексы для частых выборок."""
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tiktok_sent_sent_at ON tiktok_sent(sent_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_tiktok_username ON users(tiktok_username)")


def _migration_v6(cur: sqlite3.Cursor) -> None:
    """v5→6: tiktok_post_claims — атомарный claim/статусы доставки постов TikTok.

    Заменяет дедупликацию по tiktok_sent для мониторинга: claim до отправки
    (PK tg_id+post_id), статусы 'claimed'/'sent'/'partial'/'fallback'/'failed'
    (латиница). 'sent'/'partial'/'fallback' — не отправлять повторно; 'failed'
    разрешает перезабор после cooldown.
    """
    cur.execute(SQL_TIKTOK_POST_CLAIMS)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tiktok_claims_status ON tiktok_post_claims(status)")


def _migration_v7(cur: sqlite3.Cursor) -> None:
    """v6→7: tiktok_sessions += login_enc/password_enc — логин/пароль для playwright-входа.

    Idempotent-ALTER (как в _migration_v1): колонки добавляются только если
    отсутствуют. Значения шифруются на уровне db/tiktok_sessions (crypto).
    """
    cols = _table_columns(cur, "tiktok_sessions")
    if "login_enc" not in cols:
        cur.execute("ALTER TABLE tiktok_sessions ADD COLUMN login_enc TEXT")
    if "password_enc" not in cols:
        cur.execute("ALTER TABLE tiktok_sessions ADD COLUMN password_enc TEXT")


# Порядок применения строго соответствует номерам версий v1..v7.
MIGRATIONS: list = [_migration_v1, _migration_v2, _migration_v3, _migration_v4,
                    _migration_v5, _migration_v6, _migration_v7]


def run_migrations() -> None:
    """Применяет недостающие миграции последовательно, каждая в транзакции."""
    with DB_LOCK:
        version = get_user_version()
        if version > len(MIGRATIONS):
            logger.warning("БД новее кода: user_version=%d > %d, миграции пропущены",
                           version, len(MIGRATIONS))
            return
        for target, migration in enumerate(MIGRATIONS, start=1):
            if version >= target:
                continue
            logger.info("applying migration v%d->v%d", version, target)
            try:
                cur = conn.cursor()
                migration(cur)
                set_user_version(cur, target)
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception("migration v%d failed, откат", target)
                raise
            version = target
