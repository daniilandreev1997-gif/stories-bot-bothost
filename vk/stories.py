"""Работа с VK-сторис: получение, отправка в Telegram, проверка «сейчас».

Логика перенесена дословно из bot_host.py (подзадача A2). После отправки
новых сторис check_and_send_new_vk обновляет last_story_id через
db.update_last_story_id — как в исходном монолите (там вызов шёл без
префикса db и падал с NameError в рантайме Этапа 1; здесь баг устранён
явным импортом db).
"""
import asyncio
import logging

from telegram.ext import Application

import config
import db
from utils import format_date, safe_int

from .client import vk_call

logger = logging.getLogger(__name__)


def _is_auth_reason(reason: str) -> bool:
    """True, если причина похожа на авторизационную (error 28/5 и пр.)."""
    text = str(reason or "").lower()
    return ("vk error 28" in text or "vk error 5" in text
            or "application authorization" in text or "authorization failed" in text)


async def get_vk_stories_ex(vk_id: str) -> tuple[list, str]:
    """Сторис VK-пользователя с причиной неудачи (фикс бага №1).

    Возвращает (stories, reason): stories отсортированы по дате ([] при
    отсутствии/ошибке), reason — человекочитаемая причина ('' при успехе).
    На отсутствии токена и на VK-ошибке — logger.warning (не тихий []).
    """
    token = db.get_any_active_vk_token() or ""
    if not token:
        reason = "VK токен не задан"
        logger.warning("get_vk_stories: %s (vk_id=%s)", reason, vk_id)
        return [], reason

    ok, data, err = await vk_call(
        "stories.get",
        {
            "v": "5.131",
            "owner_id": vk_id,
            "access_token": token,
        },
    )
    if not ok:
        reason = err or "VK error"
        logger.warning("get_vk_stories: VK-ошибка (vk_id=%s): %s", vk_id, reason)
        return [], reason

    response_obj = data.get("response", {})
    if response_obj.get("count", 0) <= 0:
        return [], ""

    items = response_obj.get("items", [])
    if not items:
        return [], ""

    stories = items[0].get("stories", [])
    stories.sort(key=lambda s: s.get("date", 0))
    return stories, ""


async def get_vk_stories(vk_id: str):
    """Обёртка совместимости: только stories ([] при отсутствии/ошибке)."""
    stories, _reason = await get_vk_stories_ex(vk_id)
    return stories


async def send_stories(app: Application, tg_id: int, stories: list):
    """Отправляет сторис в Telegram; возвращает id последней успешно отправленной."""
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

            await asyncio.sleep(config.TG_SEND_DELAY_SECONDS)

        except Exception as exc:
            logger.error("Ошибка отправки VK сторис tg_id=%s: %r", tg_id, exc)

    return last_sent_id


async def check_and_send_new_vk(app: Application, tg_id: int, vk_id: str, last_story_id: str | None):
    """Отправляет только новые сторис и запоминает id последней отправленной."""
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
        db.update_last_story_id(tg_id, last_sent_id)


async def checknow_send_all_vk(app: Application, tg_id: int, vk_id: str):
    """Отправляет все текущие сторис по команде «Проверить сейчас».

    Фикс бага №1: при auth-причине (например, VK error 28 на сервисном токене)
    пользователь получает причину и подсказку (пришлите /token или войдите
    через /login), а не безликое «VK сторис сейчас нет».
    """
    stories, reason = await get_vk_stories_ex(vk_id)
    if not stories:
        if reason and _is_auth_reason(reason):
            await app.bot.send_message(
                chat_id=tg_id,
                text=(
                    f"VK сторис недоступны: {reason}\n\n"
                    "Токен не может читать сторис. Пришлите новый /token "
                    "или войдите через /login (логин/пароль)."
                ),
            )
        else:
            await app.bot.send_message(
                chat_id=tg_id,
                text=(
                    "VK сторис сейчас нет (или VK не отдает)."
                    + (f"\nПричина: {reason}" if reason else "")
                ),
            )
        return
    await send_stories(app, tg_id, stories)
