"""Чистые утилиты stories-bot-bothost: константы, время, парсинг, диагностика DNS.

Модуль зависит ТОЛЬКО от stdlib и config — никаких telegram/db/yt_dlp/asyncio,
чтобы его можно было импортировать из любого слоя без циклов.

Логирование через logging.getLogger(__name__); секреты не логируются
(в log_dns_resolution попадают только IP-адреса и имя хоста).
"""
import logging
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

import config

logger = logging.getLogger(__name__)

# =======================
# CONSTANTS
# =======================
TIKTOK_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{2,40}$")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
SKIP_EXTENSIONS = {".part", ".ytdl", ".tmp", ".temp", ".json", ".description"}
APP_BUILD = "tiktok-initial-sync-5min-2026-04-25"


# =======================
# TIMEZONE
# =======================
def get_saratov_tz():
    """ZoneInfo('Europe/Saratov'); fallback — фиксированный UTC+4 (без tzdata)."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(config.SARATOV_TZ_NAME)
        except Exception:
            pass
    return timezone(timedelta(hours=4), name=config.SARATOV_TZ_NAME)


SARATOV_TZ = get_saratov_tz()


# =======================
# UTILS
# =======================
def safe_int(value, default: int = 0) -> int:
    """int(value) без исключений; при ошибке — default."""
    try:
        return int(value)
    except Exception:
        return default


def format_date(timestamp) -> str:
    """'ДД.ММ.ГГГГ ЧЧ:ММ' в Europe/Saratov; '' для пустого/невалидного timestamp."""
    if not timestamp:
        return ""
    try:
        dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(SARATOV_TZ)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return ""


def normalize_tiktok_username(raw_text: str) -> str:
    """Извлекает и нормализует TikTok-username из ника/ссылки; '' если невалидно.

    Принимает '@nick', 'nick', 'https://tiktok.com/@nick?...', ссылки с путём.
    Результат проходит проверку TIKTOK_USERNAME_RE (2-40 символов [A-Za-z0-9._]).
    """
    text = (raw_text or "").strip()
    if not text:
        return ""

    if "tiktok.com" in text.lower():
        match = re.search(r"tiktok\.com/@([A-Za-z0-9._]+)", text, flags=re.IGNORECASE)
        if match:
            text = match.group(1)

    text = text.strip().strip("/")
    if text.startswith("@"):
        text = text[1:]
    if "/" in text:
        text = text.split("/", 1)[0]
    if "?" in text:
        text = text.split("?", 1)[0]

    text = text.strip().lower()
    if not text:
        return ""

    if not TIKTOK_USERNAME_RE.fullmatch(text):
        return ""

    return text


def extract_tiktok_post_id(post_url: str) -> str:
    """Числовой id поста из URL вида '/video/<id>'; '' если не найден."""
    if not post_url:
        return ""
    match = re.search(r"/video/(\d+)", post_url)
    return match.group(1) if match else ""


# Маркеры Netscape-формата cookie-файла (yt-dlp требует именно его).
_NETSCAPE_COOKIE_MARKERS = ("# HTTP Cookie File", "# Netscape HTTP Cookie File")


def resolve_tiktok_cookiefile() -> str | None:
    """Валидирует config.TIKTOK_COOKIES_FILE и возвращает путь для ydl_opts 'cookiefile'.

    Правила (содержимое cookies НИКОГДА не логируется):
    - не задан/пустой           -> None (без логов);
    - файл отсутствует или пустой -> warning + None (работаем без cookies);
    - первые байты не содержат
      Netscape-маркер            -> warning «формат не Netscape» + None;
    - всё ок                     -> абсолютный путь (str).
    """
    raw = (config.TIKTOK_COOKIES_FILE or "").strip()
    if not raw:
        return None

    path = Path(raw)
    try:
        if not path.is_file():
            logger.warning(
                "TikTok cookies file недоступен, работаем без него: %s", path
            )
            return None
        if path.stat().st_size <= 0:
            logger.warning(
                "TikTok cookies file пуст, работаем без него: %s", path
            )
            return None

        with open(path, "rb") as fh:
            head = fh.read(512).decode("utf-8", errors="replace")
    except OSError as exc:
        logger.warning("TikTok cookies file не читается (%s), работаем без него", exc)
        return None

    first_lines = [line.strip() for line in head.splitlines()[:5]]
    if not any(marker in line for line in first_lines for marker in _NETSCAPE_COOKIE_MARKERS):
        logger.warning(
            "TikTok cookies file: формат не Netscape, cookies не применены (%s)", path
        )
        return None

    return str(path.resolve())


def log_dns_resolution(hostname: str = "api.telegram.org") -> None:
    """Диагностика DNS: логирует уникальные IP хоста (до 8) или ошибку."""
    try:
        addr_info = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
        ip_list = []
        for _, _, _, _, sockaddr in addr_info:
            if sockaddr and len(sockaddr) >= 1:
                ip = str(sockaddr[0])
                if ip not in ip_list:
                    ip_list.append(ip)
        ip_text = ", ".join(ip_list[:8]) if ip_list else "no-addresses"
        logger.info("DNS OK | host=%s | ips=%s", hostname, ip_text)
    except Exception as exc:
        logger.error("DNS FAIL | host=%s | error=%r", hostname, exc)


# =======================
# ЭТАП 6: healthcheck runner + чанкинг media-group
# =======================
def is_heartbeat_stale(path: str | Path, stale_seconds: float, now: float | None = None) -> bool:
    """Проверяет устаревание heartbeat-файла по mtime (healthcheck runner'а).

    Правила:
    - файл не существует            -> True (нет признака жизни = устарел);
    - age = (now или time.time()) - mtime; age >= stale_seconds -> True;
    - иначе                         -> False.
    """
    file_path = Path(path)
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        return True

    reference = time.time() if now is None else now
    age = reference - mtime
    return age >= stale_seconds


def chunk_media_groups(items: list, max_items: int = 10) -> list[list]:
    """Разбивает список на чанки по max_items (лимит Telegram media-group).

    Пустой вход -> []; порядок элементов сохраняется; входной список
    не мутируется. max_items <= 0 трактуется как «без разбиения»
    (один чанк со всеми элементами).
    """
    if not items:
        return []
    if max_items <= 0:
        return [list(items)]
    return [items[i:i + max_items] for i in range(0, len(items), max_items)]
