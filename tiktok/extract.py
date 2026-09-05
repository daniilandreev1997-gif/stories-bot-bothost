"""Извлечение списка постов TikTok-профиля через yt-dlp.

Этап 3 (исправление TikTok):
- стабильный post_id: entry.id (только числовой) -> /video/<digits> из URL;
  URL как ID ЗАПРЕЩЁН — пост без числового id скипается с warning;
- cookiefile подключается только после валидации utils.resolve_tiktok_cookiefile;
- анти-rate-limit: sleep_interval_requests + ratelimit (если задан > 0).
"""
import asyncio
import functools
import logging

import config
from utils import extract_tiktok_post_id, resolve_tiktok_cookiefile, safe_int

logger = logging.getLogger(__name__)

try:
    import yt_dlp
except Exception:
    yt_dlp = None


def _build_ydl_opts(cookiefile: str | None = None) -> dict:
    """Опции yt-dlp для листинга профиля (без скачивания).

    cookiefile — опциональный путь, уже валидированный создателем
    (per-user cookies из БД или resolve_tiktok_cookiefile).
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
        "extract_flat": "in_playlist",
        "noplaylist": False,
        # Анти-rate-limit: пауза между HTTP-запросами и потолок скорости.
        "sleep_interval_requests": 0.75,
        "retries": config.HTTP_RETRIES,
        "extractor_retries": min(config.HTTP_RETRIES, 3),
        "socket_timeout": config.YTDLP_SOCKET_TIMEOUT_SECONDS,
    }

    rate_limit = safe_int(getattr(config, "TIKTOK_RATE_LIMIT_BACKOFF_SECONDS", 0), 0)
    if rate_limit > 0:
        # yt-dlp 'ratelimit' — байты/сек; интерпретируем значение как потолок.
        ydl_opts["ratelimit"] = rate_limit * 1024

    # Переданный cookiefile (per-user из БД) приоритетнее глобального:
    # параметр уже валидирован создателем; None -> fallback на resolve.
    resolved_cookiefile = cookiefile or resolve_tiktok_cookiefile()
    if resolved_cookiefile:
        ydl_opts["cookiefile"] = resolved_cookiefile

    return ydl_opts


def _resolve_post_id(entry: dict, post_url: str) -> str:
    """Стабильный числовой post_id; '' если определить нельзя.

    Порядок: entry.id (если isdigit) -> цифры /video/<digits> из URL.
    Сам URL как ID не используется (не стабильный идентификатор).
    """
    entry_id = str(entry.get("id") or "").strip()
    if entry_id.isdigit():
        return entry_id

    if post_url:
        url_id = extract_tiktok_post_id(post_url)
        if url_id:
            return url_id

    return ""


def _extract_tiktok_posts_sync(username: str, cookiefile: str | None = None) -> list[dict]:
    """Синхронное извлечение постов профиля (выполняется в executor)."""
    if yt_dlp is None:
        raise RuntimeError("yt-dlp не установлен")

    profile_url = f"https://www.tiktok.com/@{username}"
    ydl_opts = _build_ydl_opts(cookiefile)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(profile_url, download=False)

    entries = (info or {}).get("entries") or []
    posts: list[dict] = []

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue

        post_url = entry.get("webpage_url") or entry.get("url") or ""
        if not isinstance(post_url, str):
            post_url = ""
        post_url = post_url.strip()

        if post_url.startswith("/"):
            post_url = f"https://www.tiktok.com{post_url}"

        if post_url and not post_url.startswith("http"):
            if post_url.startswith("@"):
                post_url = f"https://www.tiktok.com/{post_url}"

        post_id = _resolve_post_id(entry, post_url)

        if not post_id:
            # URL как ID не используем: без числового id пост скипается.
            logger.warning(
                "TikTok post без числового id пропущен username=%s url=%s", username, post_url
            )
            continue

        if not post_url and post_id:
            post_url = f"https://www.tiktok.com/@{username}/video/{post_id}"

        if not post_url:
            logger.warning(
                "TikTok post без URL пропущен username=%s post_id=%s", username, post_id
            )
            continue

        posts.append(
            {
                "id": post_id,
                "url": post_url,
                "timestamp": safe_int(entry.get("timestamp"), 0),
                "index": index,
            }
        )

    if not posts:
        return []

    if any(p["timestamp"] > 0 for p in posts):
        posts.sort(key=lambda p: (p["timestamp"], p["index"]))
    else:
        posts = list(reversed(posts))

    for post in posts:
        post.pop("index", None)

    return posts


async def get_tiktok_posts(username: str, cookiefile: str | None = None):
    """Асинхронная обёртка: (True, posts, "") либо (False, [], причина).

    cookiefile — опциональный путь к Netscape-файлу (per-user cookies);
    None -> используется глобальный config.TIKTOK_COOKIES_FILE.
    """
    loop = asyncio.get_running_loop()
    func = functools.partial(_extract_tiktok_posts_sync, username, cookiefile)

    try:
        posts = await loop.run_in_executor(None, func)
        return True, posts, ""
    except Exception as exc:
        return False, [], f"TikTok extraction failed for @{username}: {exc}"
