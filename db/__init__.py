"""Пакет db: SQLite-слой stories-bot-bothost (соединение, миграции, доступ к данным).

Разбивка монолитного db.py на субмодули (подзадача A1):
- connection: единый sqlite3.Connection, DB_LOCK, PRAGMA, close();
- migrations: версионирование схемы (PRAGMA user_version, MIGRATIONS v1..v5);
- settings: key/value-настройки;
- users: пользователи, привязки VK/TikTok, состояние синхронизации;
- dedup: дедупликация отправленных TikTok-постов (tiktok_sent);
- vk_tokens: VK-токены/пароли пользователей (шифрование через crypto);
- instagram: учётные данные и сессии instagrapi;
- tiktok_sessions: сессии и кэш статистики TikTok.

Совместимость: старый код делает ``import db`` и вызывает db.<функция> —
все публичные имена реэкспортированы ниже. Инициализация (_set_pragmas +
run_migrations) выполняется при импорте пакета, как в старом db.py.
Секреты шифруются только через crypto.encrypt_str/decrypt_str ('enc:v1:...'),
значения секретов никогда не логируются.
"""
import logging

from .connection import DB_LOCK, BUSY_TIMEOUT_MS, _set_pragmas, close, conn, safe_int
from .migrations import (
    MIGRATIONS,
    SQL_SETTINGS,
    SQL_TIKTOK_SENT,
    SQL_USERS,
    get_user_version,
    run_migrations,
    set_user_version,
)
from .settings import get_setting, set_setting
from .users import (
    ensure_user_row,
    get_tiktok_sync_state,
    get_user_tiktok_username,
    get_user_vk_id,
    load_tiktok_users,
    load_vk_users,
    reset_tiktok_sync_state,
    save_user_tiktok_username,
    save_user_vk_id,
    set_tiktok_sync_state,
    update_last_story_id,
)
from .dedup import clear_tiktok_sent_for_user, get_tiktok_sent_ids as get_tiktok_sent_ids_legacy, mark_tiktok_post_sent
from .tiktok_claims import (
    CLAIM_STATUSES,
    claim_tiktok_post,
    get_tiktok_claim_attempts,
    get_tiktok_claim_status,
    get_tiktok_sent_ids,
    mark_tiktok_post_status,
)
from .vk_tokens import (
    delete_vk_user_token,
    get_any_active_vk_token,
    get_any_active_vk_token_with_tier,
    get_vk_user_credentials,
    get_vk_user_token,
    save_vk_user_password,
    save_vk_user_token,
)
from .instagram import (
    get_instagram_settings,
    pop_instagram_verification_session,
    save_instagram_credentials,
    save_instagram_session,
    save_instagram_verification_session,
    set_instagram_session_invalid,
)
from .tiktok_sessions import (
    get_tiktok_login,
    get_tiktok_session,
    get_tiktok_stats,
    save_tiktok_login,
    save_tiktok_session,
    set_tiktok_session_invalid,
    upsert_tiktok_stats,
)

logger = logging.getLogger(__name__)

# Инициализация при импорте: PRAGMA + миграции (замена ensure_schema()).
_set_pragmas()
run_migrations()

if __name__ == "__main__":
    # Standalone-диагностика: версия схемы, список таблиц, режим журнала.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    print("user_version:", get_user_version())
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print("tables:", tables)
    print("journal_mode:", conn.execute("PRAGMA journal_mode").fetchone()[0])
    close()
