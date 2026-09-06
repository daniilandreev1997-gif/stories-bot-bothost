"""Скачивание поста TikTok через yt-dlp во временный каталог и сборка подписи.

Этап 3 (исправление TikTok):
- формат: bv*+ba/b (склейка через ffmpeg) с merge_output_format=mp4;
  если ffmpeg недоступен — прогрессивный одиночный формат b[ext=mp4]/b;
- ffmpeg_location задаётся только если TIKTOK_FFMPEG_LOCATION реально существует;
- download_tiktok_post возвращает expected_count для photo-постов
  (эвристика partial-доставки в monitoring).

Контракт владения tmp_dir (фикс бага №2: каталог удалялся до отправки):
- владелец каталога — точка обработки поста (send_tiktok_post в monitoring),
  каталог живёт до завершения попытки отправки;
- _download_tiktok_post_sync удаляет tmp_dir сам на fail-путях
  (исключение / нет медиа); при успехе передаёт владение через
  result["tmp_dir"];
- download_tiktok_post НЕ удаляет каталог при asyncio.TimeoutError:
  executor-поток ещё пишет — «осиротевший» каталог подбирает
  sweep_stale_tiktok_tmp_dirs (вызывается в начале цикла мониторинга).
"""
import asyncio
import functools
import logging
import os
import shutil
import tempfile
import time
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

    Владение tmp_dir (фикс бага №2): fail-пути (исключение / нет медиа /
    yt-dlp не установлен) удаляют каталог здесь; при успехе каталог НЕ
    удаляется — владение передаётся вызывающему через result["tmp_dir"].
    """
    if yt_dlp is None:
        shutil.rmtree(tmp_dir, ignore_errors=True)
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

        # Fail-путь: медиа не найдены — каталог никому не нужен, чистим сами.
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {
            "ok": False,
            "error": "Не удалось определить медиафайлы в посте",
            "webpage_url": webpage_url,
        }

    except Exception as exc:
        # Fail-путь: исключение — каталог чистим сами (поток уже завершился).
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {
            "ok": False,
            "error": str(exc),
            "webpage_url": post_url,
        }


def sweep_stale_tiktok_tmp_dirs(
    max_age_seconds: int | None = None, base_dir: str | None = None
) -> int:
    """Удаляет «осиротевшие» tmp-каталоги скачивания (префикс tiktok_post_).

    Фикс бага №2, вторая половина: при asyncio-таймауте каталог не удаляется
    (executor-поток ещё пишет в него), поэтому такие каталоги подбираются
    здесь по возрасту. Вызывается в начале цикла мониторинга
    (tiktok.monitoring.check_and_send_new_tiktoks).

    Args:
        max_age_seconds: порог возраста по st_mtime (None ->
            config.TIKTOK_TMP_SWEEP_MAX_AGE_SECONDS).
        base_dir: каталог обхода (None -> tempfile.gettempdir()); параметр
            для тестов, в проде каталоги создаются через tempfile.mkdtemp.

    Returns:
        Количество удалённых каталогов (rmtree с ignore_errors=True).
    """
    if max_age_seconds is None:
        max_age_seconds = int(
            getattr(config, "TIKTOK_TMP_SWEEP_MAX_AGE_SECONDS", 3600) or 3600
        )

    root = base_dir or tempfile.gettempdir()
    cutoff = time.time() - max(0, int(max_age_seconds))
    removed = 0

    try:
        entries = os.listdir(root)
    except OSError as exc:
        logger.warning("sweep tmp-dir: не удалось прочитать %s: %r", root, exc)
        return 0

    for name in entries:
        if not name.startswith("tiktok_post_"):
            continue
        path = os.path.join(root, name)
        try:
            if not os.path.isdir(path) or os.path.islink(path):
                continue
            if os.stat(path).st_mtime >= cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
        logger.info("sweep tmp-dir: удалён осиротевший каталог %s", path)

    return removed


async def download_tiktok_post(
    post_url: str,
    timeout_seconds: int | None = None,
    cookiefile: str | None = None,
) -> dict:
    """Асинхронная обёртка над _download_tiktok_post_sync (executor + wait_for).

    Владение tmp_dir (фикс бага №2): каталог создаётся здесь; при успехе
    владение передаётся вызывающему через result["tmp_dir"] (удаление —
    в finally точки отправки, send_tiktok_post). При asyncio.TimeoutError
    каталог НЕ удаляется: executor-поток продолжает писать — такой
    «осиротевший» каталог подбирает sweep_stale_tiktok_tmp_dirs.

    cookiefile — опциональный путь к Netscape-файлу (per-user cookies);
    None -> используется глобальный config.TIKTOK_COOKIES_FILE.
    """
    loop = asyncio.get_running_loop()

    if timeout_seconds is None:
        timeout_seconds = int(getattr(config, "TIKTOK_POST_TIMEOUT_SECONDS", 240) or 240)

    tmp_dir = tempfile.mkdtemp(prefix="tiktok_post_")
    func = functools.partial(_download_tiktok_post_sync, post_url, tmp_dir, cookiefile)
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, func), timeout=timeout_seconds
        )
    except asyncio.TimeoutError:
        # Каталог не удаляем: поток ещё пишет; sweep уберёт его по возрасту.
        logger.warning(
            "Таймаут скачивания TikTok (%ss): tmp_dir оставлен для sweep: %s",
            timeout_seconds, tmp_dir,
        )
        return {
            "ok": False,
            "error": f"timeout {timeout_seconds}s",
            "webpage_url": post_url,
        }


def build_tiktok_caption(username: str, url: str, timestamp: int) -> str:
    """Подпись сообщения: 'TikTok @user', дата (если есть) и ссылка."""
    lines = [f"TikTok @{username}"]
    date_str = format_date(timestamp)
    if date_str:
        lines.append(f"📅 {date_str}")
    if url:
        lines.append(url)
    return "\n".join(lines)
