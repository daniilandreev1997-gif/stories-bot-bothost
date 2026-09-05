"""Скачивание поста TikTok через yt-dlp во временный каталог и сборка подписи.

Этап 3 (исправление TikTok):
- формат: bv*+ba/b (склейка через ffmpeg) с merge_output_format=mp4;
  если ffmpeg недоступен — прогрессивный одиночный формат b[ext=mp4]/b;
- ffmpeg_location задаётся только если TIKTOK_FFMPEG_LOCATION реально существует;
- tmp_dir создаётся в async-обёртке и передаётся в sync-функцию параметром:
  при asyncio.wait_for-таймауте coroutine отменяется, finally гарантированно
  удаляет tmp_dir;
- download_tiktok_post возвращает expected_count для photo-постов
  (эвристика partial-доставки в monitoring).
"""
import asyncio
import functools
import logging
import os
import shutil
import tempfile
from pathlib import Path

import config
from utils import (
    IMAGE_EXTENSIONS,
    SKIP_EXTENSIONS,
    VIDEO_EXTENSIONS,
    extract_tiktok_post_id,
    format_date,
    resolve_tiktok_cookiefile,
    safe_int,
)

logger = logging.getLogger(__name__)

try:
    import yt_dlp
except Exception:
    yt_dlp = None


def _resolve_ffmpeg_location() -> str:
    """Путь к ffmpeg для ydl_opts 'ffmpeg_location'; '' если не задан/не найден.

    Абсолютный/относительный путь проверяется os.path.exists; голое имя —
    через shutil.which. Не найден -> '' (yt-dlp попробует автодетект в PATH).
    """
    raw = (getattr(config, "TIKTOK_FFMPEG_LOCATION", "") or "").strip()
    if not raw:
        return ""

    if os.path.isabs(raw) or os.sep in raw or "/" in raw:
        if os.path.exists(raw):
            return raw
        logger.warning("TIKTOK_FFMPEG_LOCATION не найден, автодетект: %s", raw)
        return ""

    which = shutil.which(raw)
    if which:
        return which
    logger.warning("TIKTOK_FFMPEG_LOCATION не найден в PATH, автодетект: %s", raw)
    return ""


def _ffmpeg_available() -> bool:
    """True, если ffmpeg доступен (явная настройка или автодетект в PATH)."""
    explicit = _resolve_ffmpeg_location()
    if explicit:
        return True
    return shutil.which("ffmpeg") is not None


def _collect_downloaded_media(tmp_dir: str) -> tuple[list[str], list[str]]:
    """Собирает скачанные видео/фото из tmp_dir (отсортированные списки путей).

    Игнорируются: служебные расширения (SKIP_EXTENSIONS: .part/.ytdl/.tmp/...),
    нулевые и нечитаемые файлы.
    """
    video_files: list[str] = []
    image_files: list[str] = []

    for path in Path(tmp_dir).rglob("*"):
        if not path.is_file():
            continue

        suffix = path.suffix.lower()
        if suffix in SKIP_EXTENSIONS:
            continue

        try:
            if path.stat().st_size <= 0:
                continue
        except Exception:
            continue

        if suffix in VIDEO_EXTENSIONS:
            video_files.append(str(path))
        elif suffix in IMAGE_EXTENSIONS:
            image_files.append(str(path))

    video_files.sort()
    image_files.sort()
    return video_files, image_files


def _build_ydl_opts(tmp_dir: str, cookiefile: str | None = None) -> dict:
    """Опции yt-dlp для скачивания одного поста.

    cookiefile — опциональный путь, уже валидированный создателем
    (per-user cookies из БД или resolve_tiktok_cookiefile).
    """
    # ffmpeg доступен -> склейка bestvideo+bestaudio; иначе прогрессивный
    # одиночный формат (без склейки, yt-dlp сам упадёт с понятной ошибкой,
    # если прогрессивного нет — это обработает fallback в monitoring).
    if _ffmpeg_available():
        fmt = "bv*+ba/b[ext=mp4]/b"
        merge_format = "mp4"
    else:
        fmt = "b[ext=mp4]/b"
        merge_format = None
        logger.info("ffmpeg не найден, однопоточный прогрессивный формат")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "noplaylist": True,
        "outtmpl": os.path.join(tmp_dir, "%(id)s_%(upload_date)s_%(title).80s.%(ext)s"),
        "windowsfilenames": True,
        "format": fmt,
        "retries": config.HTTP_RETRIES,
        "fragment_retries": config.HTTP_RETRIES,
        "extractor_retries": min(config.HTTP_RETRIES, 3),
        "socket_timeout": config.YTDLP_SOCKET_TIMEOUT_SECONDS,
        # Анти-rate-limit: пауза между HTTP-запросами.
        "sleep_interval_requests": 0.75,
        "http_headers": {
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    if merge_format:
        ydl_opts["merge_output_format"] = merge_format

    ffmpeg_location = _resolve_ffmpeg_location()
    if ffmpeg_location:
        ydl_opts["ffmpeg_location"] = ffmpeg_location

    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
    else:
        # Fallback: глобальный файл из config.TIKTOK_COOKIES_FILE (валидация внутри).
        resolved = resolve_tiktok_cookiefile()
        if resolved:
            ydl_opts["cookiefile"] = resolved

    return ydl_opts


def _download_tiktok_post_sync(post_url: str, tmp_dir: str, cookiefile: str | None = None) -> dict:
    """Синхронное скачивание поста в заранее созданный tmp_dir (executor).

    Возвращает dict: ok/kind/files/tmp_dir/post_id/timestamp/webpage_url/
    expected_count (для photos) либо ok=False + error.
    tmp_dir НЕ удаляется здесь при успехе — очистка в async-обёртке (finally),
    чтобы был безопасен wait_for-таймаут.
    """
    if yt_dlp is None:
        return {"ok": False, "error": "yt-dlp не установлен", "webpage_url": post_url}

    ydl_opts = _build_ydl_opts(tmp_dir, cookiefile)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(post_url, download=True)

        info = info or {}
        post_id = str(info.get("id") or "").strip()
        webpage_url = info.get("webpage_url") or post_url
        timestamp = safe_int(info.get("timestamp"), 0)

        if not post_id:
            post_id = extract_tiktok_post_id(webpage_url) or post_url

        video_files, image_files = _collect_downloaded_media(tmp_dir)

        if video_files:
            return {
                "ok": True,
                "kind": "video",
                "files": video_files,
                "tmp_dir": tmp_dir,
                "post_id": post_id,
                "timestamp": timestamp,
                "webpage_url": webpage_url,
                "expected_count": 0,
            }

        if image_files:
            expected_count = safe_int(
                info.get("playlist_count") or info.get("n_entries"), 0
            )
            return {
                "ok": True,
                "kind": "photos",
                "files": image_files,
                "tmp_dir": tmp_dir,
                "post_id": post_id,
                "timestamp": timestamp,
                "webpage_url": webpage_url,
                "expected_count": max(0, expected_count),
            }

        return {
            "ok": False,
            "error": "Не удалось определить медиафайлы в посте",
            "webpage_url": webpage_url,
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "webpage_url": post_url,
        }


async def download_tiktok_post(
    post_url: str,
    timeout_seconds: int | None = None,
    cookiefile: str | None = None,
) -> dict:
    """Асинхронная обёртка над _download_tiktok_post_sync (executor + wait_for).

    tmp_dir создаётся здесь и удаляется в finally (в т.ч. при CancelledError
    от asyncio.wait_for — cleanup гарантирован). При таймауте возвращается
    ok=False, error="timeout <N>s".

    cookiefile — опциональный путь к Netscape-файлу (per-user cookies);
    None -> используется глобальный config.TIKTOK_COOKIES_FILE.
    """
    loop = asyncio.get_running_loop()

    if timeout_seconds is None:
        timeout_seconds = int(getattr(config, "TIKTOK_POST_TIMEOUT_SECONDS", 240) or 240)

    tmp_dir = tempfile.mkdtemp(prefix="tiktok_post_")
    try:
        func = functools.partial(_download_tiktok_post_sync, post_url, tmp_dir, cookiefile)
        return await asyncio.wait_for(
            loop.run_in_executor(None, func), timeout=timeout_seconds
        )
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "error": f"timeout {timeout_seconds}s",
            "webpage_url": post_url,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def build_tiktok_caption(username: str, url: str, timestamp: int) -> str:
    """Подпись сообщения: 'TikTok @user', дата (если есть) и ссылка."""
    lines = [f"TikTok @{username}"]
    date_str = format_date(timestamp)
    if date_str:
        lines.append(f"📅 {date_str}")
    if url:
        lines.append(url)
    return "\n".join(lines)
