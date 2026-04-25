import os
import re
import time
import socket
import sqlite3
import requests
import asyncio
import logging
import functools
import tempfile
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock

from telegram import Update, ReplyKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

try:
    import yt_dlp
except Exception:
    yt_dlp = None


# =======================
# LOGGING
# =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("VK_TIKTOK_BOT")


# =======================
# CONFIG
# =======================
API_TOKEN = os.getenv("API_TOKEN", "").strip()
VK_TOKEN_FALLBACK = os.getenv("VK_TOKEN", "").strip()

DB_NAME = "vk_stories.db"

CHECK_INTERVAL_SECONDS = 2 * 60
TOKEN_CHECK_SECONDS = 20 * 60
TIKTOK_CHECK_SECONDS = 5 * 60
TIKTOK_INITIAL_SYNC_GAP_SECONDS = 5 * 60
TG_SEND_DELAY_SECONDS = 0.35

SARATOV_TZ_NAME = "Europe/Saratov"

BUTTON_VK_ID = "VK ID"
BUTTON_TIKTOK = "TikTok @username"
BUTTON_TOKEN_VK = "Token VK"
BUTTON_CHECK_NOW = "Проверить сейчас"
BUTTON_LIST = "Кого мониторю"
BUTTON_SILENT = "Тихий режим"
BUTTON_CLEAR_TOKEN = "Сброс VK token"
BUTTON_TIKTOK_RESET = "Сброс TikTok истории"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BUTTON_VK_ID, BUTTON_TIKTOK],
        [BUTTON_TOKEN_VK, BUTTON_CLEAR_TOKEN],
        [BUTTON_CHECK_NOW, BUTTON_LIST],
        [BUTTON_TIKTOK_RESET, BUTTON_SILENT],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)

WAIT_STATE_KEYS = ("await_vk_id", "await_vk_token", "await_tiktok_username")

TIKTOK_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{2,40}$")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
SKIP_EXTENSIONS = {".part", ".ytdl", ".tmp", ".temp", ".json", ".description"}
APP_BUILD = "tiktok-initial-sync-5min-2026-04-25"


# =======================
# TIMEZONE
# =======================
def get_saratov_tz():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(SARATOV_TZ_NAME)
        except Exception:
            pass
    return timezone(timedelta(hours=4), name=SARATOV_TZ_NAME)


SARATOV_TZ = get_saratov_tz()


# =======================
# SQLITE
# =======================
DB_LOCK = Lock()
conn = sqlite3.connect(DB_NAME, check_same_thread=False)


def ensure_schema() -> None:
    with DB_LOCK:
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                vk_id TEXT,
                last_story_id TEXT,
                tiktok_username TEXT,
                tiktok_initial_sync_done INTEGER DEFAULT 0,
                tiktok_last_dispatch_ts INTEGER DEFAULT 0
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tiktok_sent (
                tg_id INTEGER NOT NULL,
                post_id TEXT NOT NULL,
                sent_at INTEGER NOT NULL,
                PRIMARY KEY (tg_id, post_id)
            )
            """
        )

        cur.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in cur.fetchall()}
        if "tiktok_username" not in columns:
            cur.execute("ALTER TABLE users ADD COLUMN tiktok_username TEXT")
        if "tiktok_initial_sync_done" not in columns:
            cur.execute("ALTER TABLE users ADD COLUMN tiktok_initial_sync_done INTEGER DEFAULT 0")
        if "tiktok_last_dispatch_ts" not in columns:
            cur.execute("ALTER TABLE users ADD COLUMN tiktok_last_dispatch_ts INTEGER DEFAULT 0")

        conn.commit()


def ensure_user_row(cur: sqlite3.Cursor, tg_id: int) -> None:
    cur.execute("SELECT tg_id FROM users WHERE tg_id = ?", (tg_id,))
    if not cur.fetchone():
        cur.execute(
            """
            INSERT INTO users (
                tg_id,
                vk_id,
                last_story_id,
                tiktok_username,
                tiktok_initial_sync_done,
                tiktok_last_dispatch_ts
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tg_id, None, None, None, 0, 0),
        )


def get_setting(key: str) -> str:
    with DB_LOCK:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
    return row[0] if row and row[0] else ""


def set_setting(key: str, value: str) -> None:
    with DB_LOCK:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()


def load_vk_users() -> list[tuple[int, str, str | None]]:
    with DB_LOCK:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tg_id, vk_id, last_story_id
            FROM users
            WHERE vk_id IS NOT NULL AND TRIM(vk_id) != ''
            """
        )
        return cur.fetchall()


def load_tiktok_users() -> list[tuple[int, str]]:
    with DB_LOCK:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tg_id, tiktok_username
            FROM users
            WHERE tiktok_username IS NOT NULL AND TRIM(tiktok_username) != ''
            """
        )
        return cur.fetchall()


def save_user_vk_id(tg_id: int, vk_id: str) -> None:
    with DB_LOCK:
        cur = conn.cursor()
        ensure_user_row(cur, tg_id)
        cur.execute(
            "UPDATE users SET vk_id = ?, last_story_id = NULL WHERE tg_id = ?",
            (vk_id, tg_id),
        )
        conn.commit()


def save_user_tiktok_username(tg_id: int, username: str) -> None:
    with DB_LOCK:
        cur = conn.cursor()
        ensure_user_row(cur, tg_id)
        cur.execute(
            "UPDATE users SET tiktok_username = ? WHERE tg_id = ?",
            (username, tg_id),
        )
        conn.commit()


def reset_tiktok_sync_state(tg_id: int) -> None:
    with DB_LOCK:
        cur = conn.cursor()
        ensure_user_row(cur, tg_id)
        cur.execute(
            """
            UPDATE users
            SET tiktok_initial_sync_done = 0,
                tiktok_last_dispatch_ts = 0
            WHERE tg_id = ?
            """,
            (tg_id,),
        )
        conn.commit()


def get_tiktok_sync_state(tg_id: int) -> tuple[bool, int]:
    with DB_LOCK:
        cur = conn.cursor()
        ensure_user_row(cur, tg_id)
        cur.execute(
            """
            SELECT tiktok_initial_sync_done, tiktok_last_dispatch_ts
            FROM users
            WHERE tg_id = ?
            """,
            (tg_id,),
        )
        row = cur.fetchone()
    if not row:
        return False, 0
    return bool(safe_int(row[0], 0)), safe_int(row[1], 0)


def set_tiktok_sync_state(
    tg_id: int,
    *,
    initial_sync_done: bool | None = None,
    last_dispatch_ts: int | None = None,
) -> None:
    updates = []
    params = []

    if initial_sync_done is not None:
        updates.append("tiktok_initial_sync_done = ?")
        params.append(1 if initial_sync_done else 0)

    if last_dispatch_ts is not None:
        updates.append("tiktok_last_dispatch_ts = ?")
        params.append(safe_int(last_dispatch_ts, 0))

    if not updates:
        return

    with DB_LOCK:
        cur = conn.cursor()
        ensure_user_row(cur, tg_id)
        params.append(tg_id)
        cur.execute(
            f"UPDATE users SET {', '.join(updates)} WHERE tg_id = ?",
            tuple(params),
        )
        conn.commit()


def update_last_story_id(tg_id: int, last_story_id: str) -> None:
    with DB_LOCK:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET last_story_id = ? WHERE tg_id = ?",
            (last_story_id, tg_id),
        )
        conn.commit()


def get_user_vk_id(tg_id: int) -> str | None:
    with DB_LOCK:
        cur = conn.cursor()
        cur.execute("SELECT vk_id FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def get_user_tiktok_username(tg_id: int) -> str | None:
    with DB_LOCK:
        cur = conn.cursor()
        cur.execute("SELECT tiktok_username FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def get_tiktok_sent_ids(tg_id: int) -> set[str]:
    with DB_LOCK:
        cur = conn.cursor()
        cur.execute("SELECT post_id FROM tiktok_sent WHERE tg_id = ?", (tg_id,))
        rows = cur.fetchall()
    return {row[0] for row in rows}


def mark_tiktok_post_sent(tg_id: int, post_id: str) -> None:
    with DB_LOCK:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO tiktok_sent (tg_id, post_id, sent_at) VALUES (?, ?, ?)",
            (tg_id, post_id, int(time.time())),
        )
        conn.commit()


def clear_tiktok_sent_for_user(tg_id: int) -> None:
    with DB_LOCK:
        cur = conn.cursor()
        cur.execute("DELETE FROM tiktok_sent WHERE tg_id = ?", (tg_id,))
        conn.commit()


ensure_schema()


# =======================
# USER INPUT STATE
# =======================
def reset_wait_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in WAIT_STATE_KEYS:
        context.user_data.pop(key, None)


def set_wait_state(context: ContextTypes.DEFAULT_TYPE, state_key: str) -> None:
    reset_wait_state(context)
    context.user_data[state_key] = True


# =======================
# SILENT MODE
# =======================
def is_silent_mode() -> bool:
    return get_setting("silent_mode") == "1"


def set_silent_mode(enable: bool) -> None:
    set_setting("silent_mode", "1" if enable else "0")


# =======================
# VK TOKEN MANAGEMENT
# =======================
def get_active_vk_token() -> str:
    override = get_setting("vk_token_override").strip()
    if override:
        return override
    return VK_TOKEN_FALLBACK.strip()


def set_token_bad_state(is_bad: bool, reason: str = "") -> None:
    if is_bad:
        set_setting("token_state", "bad")
        set_setting("token_reason", reason[:400])
    else:
        set_setting("token_state", "ok")
        set_setting("token_reason", "")


def is_token_bad() -> bool:
    return get_setting("token_state").strip() == "bad"


def get_token_bad_reason() -> str:
    return get_setting("token_reason").strip()


# =======================
# UTILS
# =======================
def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def format_date(timestamp) -> str:
    if not timestamp:
        return ""
    try:
        dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(SARATOV_TZ)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return ""


def normalize_tiktok_username(raw_text: str) -> str:
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
    if not post_url:
        return ""
    match = re.search(r"/video/(\d+)", post_url)
    return match.group(1) if match else ""


def log_dns_resolution(hostname: str = "api.telegram.org") -> None:
    try:
        addr_info = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
        ip_list = []
        for _, _, _, _, sockaddr in addr_info:
            if sockaddr and len(sockaddr) >= 1:
                ip = str(sockaddr[0])
                if ip not in ip_list:
                    ip_list.append(ip)
        ip_text = ", ".join(ip_list[:8]) if ip_list else "no-addresses"
        log.info("DNS OK | host=%s | ips=%s", hostname, ip_text)
    except Exception as exc:
        log.error("DNS FAIL | host=%s | error=%r", hostname, exc)


# =======================
# VK API
# =======================
async def vk_call(method: str, params: dict):
    url = f"https://api.vk.com/method/{method}"
    loop = asyncio.get_running_loop()

    try:
        func = functools.partial(requests.get, url, params=params, timeout=20)
        response = await loop.run_in_executor(None, func)
        data = response.json()

        if "error" in data:
            error_obj = data.get("error", {})
            code = error_obj.get("error_code")
            msg = error_obj.get("error_msg", "VK error")
            return False, data, f"VK error {code}: {msg}"

        return True, data, ""
    except Exception as exc:
        return False, {}, f"VK request failed: {exc!r}"


async def check_token_works_for_stories(vk_id_for_test: str | None = None):
    token = get_active_vk_token()
    if not token:
        return False, "VK токен не задан"

    ok, _, err = await vk_call(
        "users.get",
        {
            "v": "5.131",
            "access_token": token,
        },
    )
    if not ok:
        return False, err

    owner_id = vk_id_for_test if vk_id_for_test else "1"
    ok2, _, err2 = await vk_call(
        "stories.get",
        {
            "v": "5.131",
            "owner_id": owner_id,
            "access_token": token,
        },
    )
    if not ok2:
        return False, err2

    return True, "ok"


async def get_vk_stories(vk_id: str):
    token = get_active_vk_token()
    if not token:
        return []

    ok, data, _ = await vk_call(
        "stories.get",
        {
            "v": "5.131",
            "owner_id": vk_id,
            "access_token": token,
        },
    )
    if not ok:
        return []

    response_obj = data.get("response", {})
    if response_obj.get("count", 0) <= 0:
        return []

    items = response_obj.get("items", [])
    if not items:
        return []

    stories = items[0].get("stories", [])
    stories.sort(key=lambda s: s.get("date", 0))
    return stories


async def send_stories(app: Application, tg_id: int, stories: list):
    last_sent_id = None

    for story in stories:
        try:
            date_str = format_date(story.get("date"))
            date_caption = f"\n📅 {date_str}" if date_str else ""

            if "photo" in story:
                photo_sizes = story["photo"].get("sizes") or []
                if not photo_sizes:
                    continue
                photo_url = photo_sizes[-1].get("url")
                if not photo_url:
                    continue

                caption = f"VK сторис (фото){date_caption}"
                await app.bot.send_photo(chat_id=tg_id, photo=photo_url, caption=caption)
                last_sent_id = str(story.get("id"))

            elif "video" in story:
                files = story["video"].get("files", {})
                mp4_files = {
                    k: v
                    for k, v in files.items()
                    if isinstance(k, str) and k.startswith("mp4_")
                }

                caption = f"VK сторис (видео){date_caption}"
                if mp4_files:
                    max_key = max(mp4_files, key=lambda k: safe_int(k.split("_")[-1]))
                    video_url = mp4_files[max_key]
                    await app.bot.send_video(chat_id=tg_id, video=video_url, caption=caption)
                    last_sent_id = str(story.get("id"))
                elif isinstance(files.get("external"), dict) and files["external"].get("url"):
                    video_url = files["external"]["url"]
                    await app.bot.send_video(
                        chat_id=tg_id,
                        video=video_url,
                        caption=f"VK сторис (внешнее видео){date_caption}",
                    )
                    last_sent_id = str(story.get("id"))
                else:
                    await app.bot.send_message(
                        chat_id=tg_id,
                        text=f"Есть VK сторис-видео, но файл недоступен.{date_caption}",
                    )
                    last_sent_id = str(story.get("id"))
            else:
                await app.bot.send_message(
                    chat_id=tg_id,
                    text=f"VK сторис (неизвестный тип){date_caption}",
                )
                last_sent_id = str(story.get("id"))

            await asyncio.sleep(TG_SEND_DELAY_SECONDS)

        except Exception as exc:
            log.error("Ошибка отправки VK сторис tg_id=%s: %r", tg_id, exc)

    return last_sent_id


async def check_and_send_new_vk(app: Application, tg_id: int, vk_id: str, last_story_id: str | None):
    stories = await get_vk_stories(vk_id)
    if not stories:
        return

    if last_story_id is None:
        to_send = stories
    else:
        try:
            last_id_int = int(last_story_id)
            to_send = [s for s in stories if safe_int(s.get("id"), 0) > last_id_int]
        except Exception:
            to_send = stories

    if not to_send:
        return

    last_sent_id = await send_stories(app, tg_id, to_send)
    if last_sent_id:
        update_last_story_id(tg_id, last_sent_id)


async def checknow_send_all_vk(app: Application, tg_id: int, vk_id: str):
    stories = await get_vk_stories(vk_id)
    if not stories:
        await app.bot.send_message(chat_id=tg_id, text="VK сторис сейчас нет (или VK не отдает).")
        return
    await send_stories(app, tg_id, stories)


# =======================
# TIKTOK API (yt-dlp)
# =======================
def _extract_tiktok_posts_sync(username: str) -> list[dict]:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp не установлен")

    profile_url = f"https://www.tiktok.com/@{username}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
        "extract_flat": "in_playlist",
        "noplaylist": False,
    }

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

        post_id = str(entry.get("id") or "").strip()

        if post_url and not post_url.startswith("http"):
            if post_url.startswith("@"):
                post_url = f"https://www.tiktok.com/{post_url}"
            elif post_id:
                post_url = f"https://www.tiktok.com/@{username}/video/{post_id}"

        if not post_id and post_url:
            post_id = extract_tiktok_post_id(post_url)

        if not post_id and post_url:
            post_id = post_url

        if not post_url and post_id and post_id.isdigit():
            post_url = f"https://www.tiktok.com/@{username}/video/{post_id}"

        if not post_id or not post_url:
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


def _collect_downloaded_media(tmp_dir: str) -> tuple[list[str], list[str]]:
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


def _download_tiktok_post_sync(post_url: str) -> dict:
    if yt_dlp is None:
        return {"ok": False, "error": "yt-dlp не установлен", "webpage_url": post_url}

    tmp_dir = tempfile.mkdtemp(prefix="tiktok_post_")
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "noplaylist": True,
        "outtmpl": os.path.join(tmp_dir, "%(id)s_%(upload_date)s_%(title).80s.%(ext)s"),
        "windowsfilenames": True,
        # Prefer single-file streams to avoid ffmpeg merge dependency on shared hostings.
        "format": "best[ext=mp4]/best",
        "retries": 4,
        "fragment_retries": 4,
        "extractor_retries": 3,
        "socket_timeout": 30,
    }

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
                "files": [video_files[0]],
                "tmp_dir": tmp_dir,
                "post_id": post_id,
                "timestamp": timestamp,
                "webpage_url": webpage_url,
            }

        if image_files:
            return {
                "ok": True,
                "kind": "photos",
                "files": image_files,
                "tmp_dir": tmp_dir,
                "post_id": post_id,
                "timestamp": timestamp,
                "webpage_url": webpage_url,
            }

        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {
            "ok": False,
            "error": "Не удалось определить медиафайлы в посте",
            "webpage_url": webpage_url,
        }

    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {
            "ok": False,
            "error": str(exc),
            "webpage_url": post_url,
        }


async def get_tiktok_posts(username: str):
    loop = asyncio.get_running_loop()
    func = functools.partial(_extract_tiktok_posts_sync, username)

    try:
        posts = await loop.run_in_executor(None, func)
        return True, posts, ""
    except Exception as exc:
        return False, [], f"TikTok extraction failed for @{username}: {exc}"


async def download_tiktok_post(post_url: str) -> dict:
    loop = asyncio.get_running_loop()
    func = functools.partial(_download_tiktok_post_sync, post_url)
    return await loop.run_in_executor(None, func)


def build_tiktok_caption(username: str, url: str, timestamp: int) -> str:
    lines = [f"TikTok @{username}"]
    date_str = format_date(timestamp)
    if date_str:
        lines.append(f"📅 {date_str}")
    if url:
        lines.append(url)
    return "\n".join(lines)


async def send_tiktok_photos(app: Application, tg_id: int, files: list[str], caption: str) -> None:
    for offset in range(0, len(files), 10):
        chunk = files[offset:offset + 10]
        if not chunk:
            continue

        handles = [open(path, "rb") for path in chunk]
        media = []
        try:
            for idx, handle in enumerate(handles):
                if offset == 0 and idx == 0:
                    media.append(InputMediaPhoto(media=handle, caption=caption))
                else:
                    media.append(InputMediaPhoto(media=handle))

            await app.bot.send_media_group(chat_id=tg_id, media=media)
        finally:
            for handle in handles:
                try:
                    handle.close()
                except Exception:
                    pass

        await asyncio.sleep(TG_SEND_DELAY_SECONDS)


async def send_tiktok_fallback(app: Application, tg_id: int, username: str, post_url: str) -> bool:
    if not post_url:
        return False
    try:
        await app.bot.send_message(
            chat_id=tg_id,
            text=(
                f"TikTok @{username}: не удалось скачать пост, отправляю ссылку:\n"
                f"{post_url}"
            ),
        )
        await asyncio.sleep(TG_SEND_DELAY_SECONDS)
        return True
    except Exception as exc:
        log.error("Ошибка fallback-сообщения TikTok tg_id=%s: %r", tg_id, exc)
        return False


async def send_tiktok_post(app: Application, tg_id: int, username: str, post: dict) -> str:
    post_url = post.get("url", "")
    post_id = str(post.get("id", "")).strip()

    result = await download_tiktok_post(post_url)
    if not result.get("ok"):
        error_text = result.get("error", "unknown error")
        log.warning(
            "Не удалось скачать TikTok post tg_id=%s username=%s post_id=%s err=%s",
            tg_id,
            username,
            post_id,
            error_text,
        )
        return "fallback" if await send_tiktok_fallback(app, tg_id, username, post_url) else "failed"

    tmp_dir = result.get("tmp_dir")
    webpage_url = result.get("webpage_url") or post_url
    timestamp = safe_int(result.get("timestamp"), 0)
    caption = build_tiktok_caption(username, webpage_url, timestamp)

    try:
        kind = result.get("kind")
        files = result.get("files") or []

        if kind == "video" and files:
            with open(files[0], "rb") as video_handle:
                await app.bot.send_video(
                    chat_id=tg_id,
                    video=video_handle,
                    caption=caption,
                    supports_streaming=True,
                )
            await asyncio.sleep(TG_SEND_DELAY_SECONDS)
            return "media"

        if kind == "photos" and files:
            await send_tiktok_photos(app, tg_id, files, caption)
            return "media"

        return "fallback" if await send_tiktok_fallback(app, tg_id, username, webpage_url) else "failed"

    except Exception as exc:
        log.error(
            "Ошибка отправки TikTok tg_id=%s username=%s post_id=%s: %r",
            tg_id,
            username,
            post_id,
            exc,
        )
        return "fallback" if await send_tiktok_fallback(app, tg_id, username, webpage_url) else "failed"

    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def check_and_send_new_tiktoks(app: Application, tg_id: int, username: str):
    ok, posts, reason = await get_tiktok_posts(username)
    if not ok:
        log.warning("Ошибка чтения TikTok списка tg_id=%s username=%s: %s", tg_id, username, reason)
        return

    if not posts:
        return

    sent_ids = get_tiktok_sent_ids(tg_id)
    to_send = [post for post in posts if post.get("id") not in sent_ids]
    initial_sync_done, last_dispatch_ts = get_tiktok_sync_state(tg_id)

    if not to_send:
        if not initial_sync_done:
            set_tiktok_sync_state(tg_id, initial_sync_done=True)
            log.info("Первичная синхронизация TikTok завершена tg_id=%s username=%s", tg_id, username)
        return

    # Initial full sync mode: send exactly 1 post every 5 minutes.
    if not initial_sync_done:
        now = int(time.time())
        if last_dispatch_ts and now - last_dispatch_ts < TIKTOK_INITIAL_SYNC_GAP_SECONDS:
            return

        post = to_send[0]
        post_id = str(post.get("id", "")).strip()
        if not post_id:
            return

        delivery_state = await send_tiktok_post(app, tg_id, username, post)
        set_tiktok_sync_state(tg_id, last_dispatch_ts=int(time.time()))

        # Even fallback is considered processed to avoid duplicate re-sends.
        if delivery_state in ("media", "fallback"):
            mark_tiktok_post_sent(tg_id, post_id)
            if len(to_send) == 1:
                set_tiktok_sync_state(tg_id, initial_sync_done=True)
                log.info("Первичная синхронизация TikTok завершена tg_id=%s username=%s", tg_id, username)
        return

    # Normal mode after first full sync: only new posts.
    log.info("Найдено новых TikTok постов tg_id=%s username=%s count=%s", tg_id, username, len(to_send))
    for post in to_send:
        post_id = str(post.get("id", "")).strip()
        if not post_id:
            continue
        delivery_state = await send_tiktok_post(app, tg_id, username, post)
        if delivery_state in ("media", "fallback"):
            mark_tiktok_post_sent(tg_id, post_id)


# =======================
# BACKGROUND TASKS
# =======================
async def vk_background_checker(app: Application):
    await asyncio.sleep(10)
    while True:
        try:
            users = load_vk_users()
            if users and not is_token_bad():
                for tg_id, vk_id, last_story_id in users:
                    await check_and_send_new_vk(app, tg_id, vk_id, last_story_id)
        except Exception as exc:
            log.exception("Ошибка фоновой проверки VK: %r", exc)

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def tiktok_background_checker(app: Application):
    await asyncio.sleep(12)
    while True:
        try:
            users = load_tiktok_users()
            for tg_id, username in users:
                await check_and_send_new_tiktoks(app, tg_id, username)
        except Exception as exc:
            log.exception("Ошибка фоновой проверки TikTok: %r", exc)

        await asyncio.sleep(TIKTOK_CHECK_SECONDS)


async def token_watcher(app: Application):
    await asyncio.sleep(15)
    last_state = get_setting("token_state").strip() or "ok"

    while True:
        try:
            users = load_vk_users()
            if not users:
                await asyncio.sleep(TOKEN_CHECK_SECONDS)
                continue

            test_vk_id = users[0][1]
            ok, reason = await check_token_works_for_stories(test_vk_id)
            silent = is_silent_mode()

            if ok:
                set_token_bad_state(False)
                if last_state != "ok" and not silent:
                    for tg_id, _, _ in users:
                        try:
                            await app.bot.send_message(chat_id=tg_id, text="✅ VK токен снова работает.")
                        except Exception:
                            pass
                last_state = "ok"
            else:
                set_token_bad_state(True, reason)
                if last_state != "bad":
                    msg = (
                        "❌ VK токен не работает.\n"
                        f"{reason}\n\n"
                        "Проверка VK сторис остановлена. Пришли новый /token"
                    )
                    for tg_id, _, _ in users:
                        try:
                            await app.bot.send_message(chat_id=tg_id, text=msg)
                        except Exception:
                            pass
                last_state = "bad"

        except Exception as exc:
            log.exception("Ошибка token_watcher: %r", exc)

        await asyncio.sleep(TOKEN_CHECK_SECONDS)


# =======================
# TELEGRAM HELPERS
# =======================
async def show_main_menu(update: Update, text: str):
    await update.message.reply_text(text, reply_markup=MAIN_KEYBOARD)


async def ask_vk_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_wait_state(context, "await_vk_id")
    await show_main_menu(update, "Пришли VK ID (число).")


async def ask_vk_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_wait_state(context, "await_vk_token")
    await show_main_menu(update, "Пришли новый VK token следующим сообщением.")


async def ask_tiktok_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_wait_state(context, "await_tiktok_username")
    await show_main_menu(update, "Пришли TikTok username в формате @username или ссылкой на профиль.")


async def set_vk_token_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, token_text: str):
    new_token = (token_text or "").strip()
    if not new_token:
        await show_main_menu(update, "Пустой токен. Пришли непустое значение.")
        return

    set_setting("vk_token_override", new_token)
    set_token_bad_state(False)
    reset_wait_state(context)
    await show_main_menu(update, "✅ VK token обновлен.")


async def set_vk_id_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, vk_id_text: str):
    text = (vk_id_text or "").strip()
    if not (text.isdigit() or (text.startswith("-") and text[1:].isdigit())):
        await show_main_menu(update, "VK ID должен быть числом.")
        return

    tg_id = update.message.chat_id
    save_user_vk_id(tg_id, text)
    reset_wait_state(context)
    await show_main_menu(update, f"✅ Мониторю VK ID: {text}")


async def set_tiktok_username_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_username: str):
    username = normalize_tiktok_username(raw_username)
    if not username:
        await show_main_menu(update, "Не смог распознать TikTok username. Пример: @iozb8")
        return

    tg_id = update.message.chat_id
    old_username = get_user_tiktok_username(tg_id)

    save_user_tiktok_username(tg_id, username)
    if old_username != username:
        clear_tiktok_sent_for_user(tg_id)
        reset_tiktok_sync_state(tg_id)

    reset_wait_state(context)
    await show_main_menu(
        update,
        f"✅ Мониторю TikTok @{username}. Запускаю первую синхронизацию (загружу доступные посты).",
    )

    asyncio.create_task(check_and_send_new_tiktoks(context.application, tg_id, username))


# =======================
# TELEGRAM HANDLERS
# =======================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_wait_state(context)
    await show_main_menu(
        update,
        (
            "Бот мониторит VK сторис и TikTok.\n\n"
            "Кнопки:\n"
            f"• {BUTTON_VK_ID} — сохранить VK ID\n"
            f"• {BUTTON_TIKTOK} — сохранить TikTok username\n"
            f"• {BUTTON_TOKEN_VK} — следующий текст сохранится как VK token\n"
            f"• {BUTTON_CHECK_NOW} — проверка сейчас\n"
            f"• {BUTTON_LIST} — показать, кого мониторим\n"
            f"• {BUTTON_TIKTOK_RESET} — заново загрузить TikTok\n"
            f"• {BUTTON_SILENT} — вкл/выкл тех. уведомления\n"
            f"• {BUTTON_CLEAR_TOKEN} — сброс override токена\n\n"
            "Slash-команды тоже работают: /checknow /list /silent /who /token /tiktok /tiktokreset /cleartoken"
        ),
    )


async def silent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_wait_state(context)
    new_state = not is_silent_mode()
    set_silent_mode(new_state)

    status = "ВКЛЮЧЕНА (тех. уведомлений не будет)" if new_state else "ВЫКЛЮЧЕНА (уведомления будут приходить)"
    await show_main_menu(update, f"🤫 Тишина {status}")


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_wait_state(context)
    tg_id = update.message.chat_id

    vk_id = get_user_vk_id(tg_id)
    tiktok_username = get_user_tiktok_username(tg_id)

    lines = []
    if vk_id:
        lines.append(f"• VK ID: {vk_id}")
    else:
        lines.append("• VK ID: не задан")

    if tiktok_username:
        lines.append(f"• TikTok: @{tiktok_username}")
    else:
        lines.append("• TikTok: не задан")

    token_state = "плохой" if is_token_bad() else "ok"
    lines.append(f"• VK token status: {token_state}")

    await show_main_menu(update, "Кого мониторим:\n" + "\n".join(lines))


async def who_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_wait_state(context)
    tg_id = update.message.chat_id
    vk_id = get_user_vk_id(tg_id)

    if not vk_id:
        await show_main_menu(update, "Сначала укажи VK ID.")
        return

    ok, reason = await check_token_works_for_stories(vk_id)
    if ok:
        await show_main_menu(update, "✅ VK токен работает.")
    else:
        await show_main_menu(update, f"❌ Проблема с VK токеном: {reason}")


async def checknow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_wait_state(context)
    tg_id = update.message.chat_id

    vk_id = get_user_vk_id(tg_id)
    tiktok_username = get_user_tiktok_username(tg_id)

    if not vk_id and not tiktok_username:
        await show_main_menu(update, "Сначала укажи VK ID или TikTok username.")
        return

    if vk_id:
        await show_main_menu(update, "Проверяю VK...")
        await checknow_send_all_vk(context.application, tg_id, vk_id)

    if tiktok_username:
        await show_main_menu(update, f"Проверяю TikTok @{tiktok_username}...")
        await check_and_send_new_tiktoks(context.application, tg_id, tiktok_username)

    await show_main_menu(update, "Готово.")


async def token_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await ask_vk_token(update, context)
        return

    await set_vk_token_from_text(update, context, " ".join(args))


async def cleartoken_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_wait_state(context)
    set_setting("vk_token_override", "")
    set_token_bad_state(False)
    await show_main_menu(update, "✅ VK token override сброшен. Используется fallback токен.")


async def tiktok_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await ask_tiktok_username(update, context)
        return

    await set_tiktok_username_from_text(update, context, " ".join(args))


async def tiktokreset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_wait_state(context)
    tg_id = update.message.chat_id
    username = get_user_tiktok_username(tg_id)

    if not username:
        await show_main_menu(update, "Сначала укажи TikTok username.")
        return

    clear_tiktok_sent_for_user(tg_id)
    reset_tiktok_sync_state(tg_id)
    await show_main_menu(update, f"✅ Сбросил историю TikTok для @{username}. Запускаю повторную загрузку.")
    asyncio.create_task(check_and_send_new_tiktoks(context.application, tg_id, username))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if not text:
        await show_main_menu(update, "Пустое сообщение. Выбери кнопку или пришли значение.")
        return

    # Buttons
    if text == BUTTON_VK_ID:
        await ask_vk_id(update, context)
        return

    if text == BUTTON_TIKTOK:
        await ask_tiktok_username(update, context)
        return

    if text == BUTTON_TOKEN_VK:
        await ask_vk_token(update, context)
        return

    if text == BUTTON_CHECK_NOW:
        await checknow_cmd(update, context)
        return

    if text == BUTTON_LIST:
        await list_cmd(update, context)
        return

    if text == BUTTON_TIKTOK_RESET:
        await tiktokreset_cmd(update, context)
        return

    if text == BUTTON_SILENT:
        await silent_cmd(update, context)
        return

    if text == BUTTON_CLEAR_TOKEN:
        await cleartoken_cmd(update, context)
        return

    # Awaited states
    if context.user_data.get("await_vk_id"):
        await set_vk_id_from_text(update, context, text)
        return

    if context.user_data.get("await_vk_token"):
        await set_vk_token_from_text(update, context, text)
        return

    if context.user_data.get("await_tiktok_username"):
        await set_tiktok_username_from_text(update, context, text)
        return

    # Backward-compatible free text behavior
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        await set_vk_id_from_text(update, context, text)
        return

    possible_username = normalize_tiktok_username(text)
    if possible_username:
        await set_tiktok_username_from_text(update, context, possible_username)
        return

    await show_main_menu(
        update,
        (
            "Не понял ввод. Выбери кнопку или пришли:\n"
            "• VK ID числом\n"
            "• TikTok username в формате @username\n"
            "• VK token (после кнопки Token VK)"
        ),
    )


async def post_init(application: Application):
    loop = asyncio.get_event_loop()
    loop.create_task(vk_background_checker(application))
    loop.create_task(token_watcher(application))
    loop.create_task(tiktok_background_checker(application))


def main():
    if not API_TOKEN:
        print("API_TOKEN не найден")
        return

    log.info("Starting bot | build=%s | file=%s | pid=%s", APP_BUILD, __file__, os.getpid())
    log_dns_resolution("api.telegram.org")

    app = Application.builder().token(API_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("checknow", checknow_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("silent", silent_cmd))
    app.add_handler(CommandHandler("who", who_cmd))
    app.add_handler(CommandHandler("token", token_cmd))
    app.add_handler(CommandHandler("cleartoken", cleartoken_cmd))
    app.add_handler(CommandHandler("tiktok", tiktok_cmd))
    app.add_handler(CommandHandler("tiktokreset", tiktokreset_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.post_init = post_init
    app.run_polling(drop_pending_updates=True, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
