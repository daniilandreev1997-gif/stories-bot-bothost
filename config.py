"""Централизованная конфигурация stories-bot-bothost.

Порядок загрузки настроек:
1. Парсится файл ``.env``, лежащий рядом с этим модулем (``BASE_DIR/.env``).
   Значения из него заносятся в ``os.environ`` ТОЛЬКО если ключ ещё не задан,
   поэтому реальные переменные окружения всегда имеют приоритет над ``.env``.
2. Далее все настройки читаются стандартным ``os.getenv``.

Обязательные параметры (старт падает с ValueError, если пусто/невалидно):
- ``API_TOKEN`` — токен Telegram-бота от @BotFather;
- ``STORIES_ENCRYPTION_KEY`` — Fernet-ключ шифрования пользовательских секретов.

Секреты никогда не логируются и не выводятся: в лог пишется только длина
токена и не-секретные поля (пути, интервалы).
"""

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# Каталог, в котором лежит бот (рядом с ним ожидается .env).
BASE_DIR = Path(__file__).resolve().parent


def _strip_quotes(raw: str) -> str:
    """Снимает парные одинарные/двойные кавычки вокруг значения."""
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    return raw


def _load_env_file(path: Path) -> None:
    """Читает строки KEY=VALUE из ``path`` в ``os.environ``.

    - пустые строки и ``#``-комментарии игнорируются;
    - кавычки вокруг значения снимаются;
    - ключ заносится в ``os.environ`` ТОЛЬКО если ещё не задан
      (``setdefault`` — реальные env не перезаписываются);
    - отсутствие файла — warning в лог, не ошибка.
    """
    if not path.is_file():
        logger.warning(".env не найден рядом с ботом: %s (используются переменные окружения)", path)
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("Не удалось прочитать %s: %s", path, exc)
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key:
            continue
        os.environ.setdefault(key, _strip_quotes(value.strip()))


# Загружаем .env рядом с ботом ДО чтения настроек ниже.
_load_env_file(BASE_DIR / ".env")


def _get_int(name: str, default: int) -> int:
    """Читает int-переменную окружения; пусто/невалидно -> default + warning."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r не является int, используется default=%d", name, raw, default)
        return default


def _get_float(name: str, default: float) -> float:
    """Читает float-переменную окружения; пусто/невалидно -> default + warning."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r не является float, используется default=%s", name, raw, default)
        return default


# --- Обязательные параметры --------------------------------------------------

# Telegram-токен бота (от @BotFather). ЗНАЧЕНИЕ никогда не логировать!
API_TOKEN = os.getenv("API_TOKEN", "").strip()
if not API_TOKEN:
    raise ValueError("API_TOKEN не задан: заполни .env рядом с ботом (см. .env.example)")

# Fernet-ключ шифрования пользовательских секретов. ЗНАЧЕНИЕ никогда не логировать!
STORIES_ENCRYPTION_KEY = os.getenv("STORIES_ENCRYPTION_KEY", "").strip()
try:
    FERNET = Fernet(STORIES_ENCRYPTION_KEY)
except Exception as exc:
    raise ValueError(
        "STORIES_ENCRYPTION_KEY не является валидным Fernet-ключом. "
        "Сгенерируй ключ командой: "
        'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    ) from exc

# --- Необязательные параметры (с безопасными дефолтами) ----------------------

VK_TOKEN = os.getenv("VK_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "vk_stories.db").strip() or "vk_stories.db"

CHECK_INTERVAL_SECONDS = _get_int("CHECK_INTERVAL_SECONDS", 120)
TOKEN_CHECK_SECONDS = _get_int("TOKEN_CHECK_SECONDS", 1200)
TIKTOK_CHECK_SECONDS = _get_int("TIKTOK_CHECK_SECONDS", 300)
TIKTOK_INITIAL_SYNC_GAP_SECONDS = _get_int("TIKTOK_INITIAL_SYNC_GAP_SECONDS", 300)
TG_SEND_DELAY_SECONDS = _get_float("TG_SEND_DELAY_SECONDS", 0.35)

VK_API_TIMEOUT_SECONDS = _get_int("VK_API_TIMEOUT_SECONDS", 20)
HTTP_RETRIES = _get_int("HTTP_RETRIES", 4)
YTDLP_SOCKET_TIMEOUT_SECONDS = _get_int("YTDLP_SOCKET_TIMEOUT_SECONDS", 30)

USER_AGENT = (
    os.getenv("USER_AGENT", "").strip()
    or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIKTOK_MAX_MEDIA_PER_USER = _get_int("TIKTOK_MAX_MEDIA_PER_USER", 0)  # 0 = без лимита

LOG_LEVEL = (os.getenv("LOG_LEVEL", "INFO").strip() or "INFO").upper()

# --- Опциональные параметры для будущих сервисов (этап 2) --------------------

VK_DIRECT_AUTH_CLIENT_ID = os.getenv("VK_DIRECT_AUTH_CLIENT_ID", "").strip()
VK_DIRECT_AUTH_CLIENT_SECRET = os.getenv("VK_DIRECT_AUTH_CLIENT_SECRET", "").strip()
VK_DIRECT_AUTH_SCOPE = os.getenv("VK_DIRECT_AUTH_SCOPE", "stories").strip()
INSTAGRAM_SESSIONS_DIR = os.getenv("INSTAGRAM_SESSIONS_DIR", "./ig_sessions").strip()
TIKTOK_COOKIES_FILE = os.getenv("TIKTOK_COOKIES_FILE", "").strip()

# Путь к ffmpeg для склейки bv*+ba (TikTok). Пусто = автодетект в PATH.
TIKTOK_FFMPEG_LOCATION = os.getenv("TIKTOK_FFMPEG_LOCATION", "").strip()

# Таймаут на один пост (скачивание + отправка), секунды.
TIKTOK_POST_TIMEOUT_SECONDS = _get_int("TIKTOK_POST_TIMEOUT_SECONDS", 240)

# Бюджет всего цикла мониторинга (все пользователи), секунды.
TIKTOK_CYCLE_TIMEOUT_SECONDS = _get_int("TIKTOK_CYCLE_TIMEOUT_SECONDS", 900)

# Пауза при rate-limit (HTTP 429 / TikTok), секунды.
TIKTOK_RATE_LIMIT_BACKOFF_SECONDS = _get_int("TIKTOK_RATE_LIMIT_BACKOFF_SECONDS", 300)

# --- Этап 6 «Планировщик и оптимизация» --------------------------------------

# Таймаут graceful shutdown планировщика источников, секунды.
SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS = _get_int("SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS", 15)

# Минимальный интервал между запросами к одному источнику (rate limit), секунды.
VK_SOURCE_MIN_INTERVAL_SECONDS = _get_float("VK_SOURCE_MIN_INTERVAL_SECONDS", 1.0)
TIKTOK_SOURCE_MIN_INTERVAL_SECONDS = _get_float("TIKTOK_SOURCE_MIN_INTERVAL_SECONDS", 2.0)

# Heartbeat healthcheck-файла runner'а: период записи и порог «устаревания».
RUNNER_HEARTBEAT_SECONDS = _get_int("RUNNER_HEARTBEAT_SECONDS", 120)
RUNNER_HEARTBEAT_STALE_SECONDS = _get_int("RUNNER_HEARTBEAT_STALE_SECONDS", 300)

# Лимит Telegram media-group (макс. медиа в одном альбоме) для чанкинга.
TG_MEDIA_GROUP_MAX_ITEMS = _get_int("TG_MEDIA_GROUP_MAX_ITEMS", 10)

# База экспоненциального backoff между внешними ретраями скачивания.
TIKTOK_RETRY_BACKOFF_BASE_SECONDS = _get_float("TIKTOK_RETRY_BACKOFF_BASE_SECONDS", 2.0)

# Максимум паузы экспоненциального backoff, секунды.
TIKTOK_RETRY_BACKOFF_MAX_SECONDS = _get_int("TIKTOK_RETRY_BACKOFF_MAX_SECONDS", 120)

# Включить вход в TikTok по логину/паролю (playwright, опционально; отдельная подзадача).
TIKTOK_LOGIN_ENABLED = (os.getenv("TIKTOK_LOGIN_ENABLED", "0").strip() == "1")
# Headless-режим браузера для playwright-логина (1 = без окна).
TIKTOK_LOGIN_HEADLESS = (os.getenv("TIKTOK_LOGIN_HEADLESS", "1").strip() == "1")
# ВАЖНО: TIKTOK_LOGIN_EMAIL / TIKTOK_LOGIN_PASSWORD глобально в config не читаются —
# учётные данные хранятся per-user в БД (шифрование через crypto).

# Базовый URL стороннего сайта-парсера для просмотра IG-сторис (пакет instagram/).
# Пусто = сервис не настроен; используется будущей реализацией instagram.viewer.fetch_stories.
INSTAGRAM_VIEWER_BASE_URL = os.getenv("INSTAGRAM_VIEWER_BASE_URL", "").strip()

# --- Не-секретные константы ---------------------------------------------------

# Часовой пояс для отображения времени в сообщениях бота.
SARATOV_TZ_NAME = "Europe/Saratov"

logger.info("config loaded | api_token_len=%d | db=%s", len(API_TOKEN), DB_PATH)
